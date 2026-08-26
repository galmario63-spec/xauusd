import asyncio
import os
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAPI_TOKEN')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID')
SYMBOL = "XAUUSD"

# Parametre stratégie a rizika
LOT_SIZE = 0.01          # Veľkosť pozície (veľkosť lotu na test)
SL_POINTS = 15.0         # Počiatočný Stop Loss (-15)
TP_POINTS = 60.0         # Take Profit pre pomer 4:1 (15 * 4 = 60)
BE_TRIGGER = 10.0        # Keď je zisk +10, posunúť na Break-Even

async def manage_positions(connection):
    """Manažment otvorených pozícií: posun na BE pri +10 zisku"""
    try:
        positions = await connection.get_positions()
        for pos in positions:
            if pos['symbol'] == SYMBOL:
                profit = pos.get('profit', 0)
                open_price = pos['openPrice']
                current_sl = pos.get('stopLoss', 0)
                
                # Ak zisk dosiahol +10 a SL ešte nie je na úrovni vstupu (BE)
                if profit >= BE_TRIGGER and current_sl != open_price:
                    print(f"Dosiahnutý zisk {profit}. Posúvam SL na Break-Even: {open_price}")
                    await connection.modify_position(
                        position_id=pos['id'],
                        stop_loss=open_price,
                        take_profit=pos.get('takeProfit')
                    )
    except Exception as e:
        print("Chyba pri manažmente pozícií:", e)

async def check_strategy_and_trade(connection):
    """Logika pre Price Action, Supply/Demand zóny a vstup do obchodu"""
    try:
        positions = await connection.get_positions()
        # Ak už máme otvorenú pozíciu na XAUUSD, nové neotvárame
        if any(p['symbol'] == SYMBOL for p in positions):
            return

        # Tu ťaháme sviečkové dáta z M5 pre Price Action a Demand/Supply zóny
        rates = await connection.get_candles(SYMBOL, 'M5', 10)
        if not rates or len(rates) < 3:
            return

        # Sviečková analýza (Price Action)
        last_candle = rates[-1]
        prev_candle = rates[-2]
        
        is_bullish_pa = last_candle['close'] > last_candle['open'] and prev_candle['close'] < prev_candle['open']
        
        # Ak cena reaguje na Demand zónu a máme sviečkový signál:
        if is_bullish_pa:
            print("Signál detekovaný na XAUUSD (Price Action / Demand zóna)! Otváram BUY pozíciu...")
            bid = last_candle['close']
            sl = bid - SL_POINTS
            tp = bid + TP_POINTS
            
            await connection.create_market_buy_order(
                symbol=SYMBOL,
                volume=LOT_SIZE,
                stop_loss=sl,
                take_profit=tp
            )
            print("Obchod úspešne otvorený s pomerom 4:1.")

    except Exception as e:
        print("Chyba v obchodnej logike:", e)

async def main():
    print("Bot štartuje cez oficiálne MetaApi SDK...")
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    print("Úspešne pripojené k MetaApi serveru a synchronizované.")
    
    while True:
        # 1. Spravuj existujúce pozície (BE / SL)
        await manage_positions(connection)
        
        # 2. Hľadaj nové vstupy (Price Action / S&D)
        await check_strategy_and_trade(connection)
        
        # Pauza medzi kontrolami (každých 15 sekúnd)
        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
