import os
import time
import asyncio
from datetime import datetime, timedelta, timezone
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"
TIMEFRAME = "5m"

FIB_MIN = 0.50
FIB_MAX = 0.618
RISK_REWARD_RATIO = 3.0
LOT_SIZE = 0.01

async def main():
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    # Inicializácia klienta pre historické sviečky (správny spôsob v MetaApi SDK)
    historical_data = metaapi.metatrader_account_api.get_historical_data_client(ACCOUNT_ID)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    print(f"Riobot úspešne beží a monitoruje {SYMBOL}...")

    while True:
        try:
            # Sťahovanie sviečok cez oficiálne historické API
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=6)
            candles = await historical_data.get_candles(SYMBOL, TIMEFRAME, start_time, end_time)
            
            if not candles or len(candles) < 20:
                await asyncio.sleep(15)
                continue

            highs = [c['high'] for c in candles]
            lows = [c['low'] for c in candles]
            
            max_price = max(highs[-20:])
            min_price = min(lows[-20:])
            diff = max_price - min_price
            
            if diff == 0:
                await asyncio.sleep(15)
                continue

            fib_50 = max_price - (diff * FIB_MIN)
            fib_618 = max_price - (diff * FIB_MAX)
            current_price = candles[-1]['close']
            
            positions = await connection.get_positions()
            has_position = any(p['symbol'] == SYMBOL for p in positions)

            if not has_position:
                if min_price < current_price and (min(fib_50, fib_618) <= current_price <= max(fib_50, fib_618)):
                    print(f"Cena {current_price} je v BUY Fib zóne. Otváram...")
                    sl = current_price - (diff * 0.3)
                    tp = current_price + ((current_price - sl) * RISK_REWARD_RATIO)
                    await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)

                elif max_price > current_price and (min(fib_50, fib_618) <= current_price <= max(fib_50, fib_618)):
                    print(f"Cena {current_price} je v SELL Fib zóne. Otváram...")
                    sl = current_price + (diff * 0.3)
                    tp = current_price - ((sl - current_price) * RISK_REWARD_RATIO)
                    await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp)

            await asyncio.sleep(20)

        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
