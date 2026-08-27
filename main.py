import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import asyncio
import logging
from datetime import datetime, timedelta
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TOKEN = os.getenv("METAAPI_TOKEN")

SYMBOL = "XAUUSD"
LOT_TP1 = 0.20  # 3 obchody
LOT_TP2 = 0.10  # 1 obchod
SL_POINTS = 300
TP_POINTS = 450
BE_TRIGGER_POINTS = 150  # Posun na Break-Even po 150 bodoch zisku

async def main():
    if not TOKEN or not ACCOUNT_ID:
        logger.error("Chýba METAAPI_TOKEN alebo METAAPI_ACCOUNT_ID v premenných Railway!")
        return

    logger.info("Spúšťam bota s nastavením TP1 (3x 0.20) a TP2 (1x 0.10)...")
    
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    logger.info("Pripojenie aktívne, monitorujem trh a pozície...")

    while True:
        try:
            # 1. Správa Break-Even pre otvorené pozície
            positions = await connection.get_positions()
            price_info = await connection.get_symbol_price(SYMBOL)
            
            for pos in positions:
                if pos['symbol'] == SYMBOL:
                    open_price = pos['openPrice']
                    current_sl = pos.get('stopLoss', 0)
                    
                    if pos['type'] == 'POSITION_TYPE_BUY':
                        current_bid = price_info['bid']
                        if current_bid >= open_price + (BE_TRIGGER_POINTS * 0.1) and current_sl < open_price:
                            await connection.modify_position(pos['id'], stop_loss=open_price, take_profit=pos.get('takeProfit'))
                            logger.info(f"Break-Even aktivovaný pre BUY pozíciu #{pos['id']}")
                            
                    elif pos['type'] == 'POSITION_TYPE_SELL':
                        current_ask = price_info['ask']
                        if current_ask <= open_price - (BE_TRIGGER_POINTS * 0.1) and (current_sl > open_price or current_sl == 0):
                            await connection.modify_position(pos['id'], stop_loss=open_price, take_profit=pos.get('takeProfit'))
                            logger.info(f"Break-Even aktivovaný pre SELL pozíciu #{pos['id']}")

            # 2. Ochrana proti duplicite - ak už existujú pozície, neotvárame nové
            has_open_positions = any(p['symbol'] == SYMBOL for p in positions)
            if has_open_positions:
                await asyncio.sleep(15)
                continue

            # 3. Kontrola sviečok a Engulfing signálu
            start_time = datetime.utcnow() - timedelta(hours=2)
            candles = await account.get_historical_candles(SYMBOL, '5m', start_time, 3)
            
            if not candles or len(candles) < 2:
                await asyncio.sleep(10)
                continue
                
            prev_c = candles[-2]
            curr_c = candles[-1]
            
            bullish = (curr_c['close'] > curr_c['open'] and 
                       prev_c['close'] < prev_c['open'] and 
                       curr_c['close'] >= prev_c['open'] and 
                       curr_c['open'] <= prev_c['close'])
                       
            bearish = (curr_c['close'] < curr_c['open'] and 
                       prev_c['close'] > prev_c['open'] and 
                       curr_c['close'] <= prev_c['open'] and 
                       curr_c['open'] >= prev_c['close'])
            
            if bullish:
                logger.info("Bullish Engulfing - otváram 3x TP1 (0.20) a 1x TP2 (0.10) BUY!")
                ask = price_info['ask']
                sl = ask - (SL_POINTS * 0.1)
                tp = ask + (TP_POINTS * 0.1)
                
                # 3x TP1 (0.20 lotu)
                for _ in range(3):
                    await connection.create_market_buy_order(SYMBOL, LOT_TP1, stop_loss=sl, take_profit=tp)
                    await asyncio.sleep(0.3)
                # 1x TP2 (0.10 lotu)
                await connection.create_market_buy_order(SYMBOL, LOT_TP2, stop_loss=sl, take_profit=tp)
                logger.info("Všetky BUY príkazy úspešne odoslané.")
                
            elif bearish:
                logger.info("Bearish Engulfing - otváram 3x TP1 (0.20) a 1x TP2 (0.10) SELL!")
                bid = price_info['bid']
                sl = bid + (SL_POINTS * 0.1)
                tp = bid - (TP_POINTS * 0.1)
                
                # 3x TP1 (0.20 lotu)
                for _ in range(3):
                    await connection.create_market_sell_order(SYMBOL, LOT_TP1, stop_loss=sl, take_profit=tp)
                    await asyncio.sleep(0.3)
                # 1x TP2 (0.10 lotu)
                await connection.create_market_sell_order(SYMBOL, LOT_TP2, stop_loss=sl, take_profit=tp)
                logger.info("Všetky SELL príkazy úspešne odoslané.")

            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"Chyba v cykle: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
