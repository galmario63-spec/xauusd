import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import asyncio
import logging
from datetime import datetime, timedelta
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ACCOUNT_ID = "6ec9b96d-841d-4c6c-8290-7d35114704b2"
TOKEN = os.getenv("METAAPI_TOKEN")

SYMBOL = "XAUUSD"
LOT_TP1 = 0.20
LOT_TP2 = 0.10
SL_POINTS = 300

async def main():
    if not TOKEN:
        logger.error("Chýba METAAPI_TOKEN v premenných Railway!")
        return

    logger.info("Spúšťam reálneho MetaApi bota pre RoboForex ProCent...")
    
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    logger.info("Úspešne pripojené k RoboForex ProCent cez MetaApi!")

    while True:
        try:
            candles = await connection.get_historical_candles(SYMBOL, '5m', datetime.now() - timedelta(hours=1), 3)
            if len(candles) < 2:
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
            
            price_info = await connection.get_symbol_price(SYMBOL)
            
            if bullish:
                logger.info("Bullish Engulfing - otváram BUY na ProCent účte!")
                ask = price_info['ask']
                sl = ask - (SL_POINTS * 0.1)
                
                for _ in range(3):
                    await connection.create_market_buy_order(SYMBOL, LOT_TP1, stop_loss=sl)
                    await asyncio.sleep(0.3)
                await connection.create_market_buy_order(SYMBOL, LOT_TP2, stop_loss=sl)
                logger.info("BUY príkazy úspešne odoslané.")
                
            elif bearish:
                logger.info("Bearish Engulfing - otváram SELL na ProCent účte!")
                bid = price_info['bid']
                sl = bid + (SL_POINTS * 0.1)
                
                for _ in range(3):
                    await connection.create_market_sell_order(SYMBOL, LOT_TP1, stop_loss=sl)
                    await asyncio.sleep(0.3)
                await connection.create_market_sell_order(SYMBOL, LOT_TP2, stop_loss=sl)
                logger.info("SELL príkazy úspešne odoslané.")

            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"Chyba: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
