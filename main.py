import os
import time
import asyncio
from metaapi_cloud_sdk import MetaApi

# Konfigurácia z premenných prostredia alebo priamo
TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"
TIMEFRAME = "5m"

# Parametre stratégie a vylepšený väčší Take Profit (väčší Risk/Reward)
FIB_MIN = 0.50
FIB_MAX = 0.618
RISK_REWARD_RATIO = 3.0  # Zväčšený cieľ pre TP (predtým napr. 1.5 alebo 2.0)
LOT_SIZE = 0.01

async def main():
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    # Pripojenie k účtu a čakanie na deployment
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    print(f"Riobot je úspešne spustený a monitoruje {SYMBOL} na {TIMEFRAME}...")

    while True:
        try:
            # Sťahovanie posledných sviečok pre výpočet Fibonacciho úrovní
            candles = await connection.get_candles(SYMBOL, TIMEFRAME, 50)
            if not candles or len(candles) < 20:
                await asyncio.sleep(10)
                continue

            highs = [c['high'] for c in candles]
            lows = [c['low'] for c in candles]
            
            max_price = max(highs[-20:])
            min_price = min(lows[-20:])
            diff = max_price - min_price
            
            if diff == 0:
                await asyncio.sleep(10)
                continue

            # Výpočet Fibonacciho zóny pre korekciu
            fib_50 = max_price - (diff * FIB_MIN)
            fib_618 = max_price - (diff * FIB_MAX)
            
            current_price = candles[-1]['close']
            
            # Kontrola otvorených pozícií
            positions = await connection.get_positions()
            has_position = any(p['symbol'] == SYMBOL for p in positions)

            if not has_position:
                # Logika pre BUY (ak cena korigovala do Fib zóny zdola nahor alebo v nej tancuje)
                if min_price < current_price and (min(fib_50, fib_618) <= current_price <= max(fib_50, fib_618)):
                    print(f"Cena {current_price} je v nákupnej Fib zóne. Otváram BUY pozíciu...")
                    sl = current_price - (diff * 0.3)
                    tp = current_price + ((current_price - sl) * RISK_REWARD_RATIO)
                    
                    await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)

                # Logika pre SELL (ak cena korigovala do Fib zóny zhora nadol)
                elif max_price > current_price and (min(fib_50, fib_618) <= current_price <= max(fib_50, fib_618)):
                    print(f"Cena {current_price} je v predajnej Fib zóne. Otváram SELL pozíciu...")
                    sl = current_price + (diff * 0.3)
                    tp = current_price - ((sl - current_price) * RISK_REWARD_RATIO)
                    
                    await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp)

            await asyncio.sleep(15)  # Kontrola každých 15 sekúnd

        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
