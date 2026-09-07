import os
import time
import asyncio
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"

FIB_MIN = 0.50
FIB_MAX = 0.618
RISK_REWARD_RATIO = 3.0
LOT_SIZE = 0.01

# Pamäť na zbieranie živých cien pre výpočet zóny
price_history = []

async def main():
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    print(f"Riobot je ostrý, zbiera dáta a začína obchodovať {SYMBOL}...")

    while True:
        try:
            price_data = await connection.get_symbol_price(SYMBOL)
            if not price_data:
                await asyncio.sleep(15)
                continue

            current_price = price_data.get('bid')
            if not current_price:
                await asyncio.sleep(15)
                continue

            # Ukladáme aktuálnu cenu do pamäte
            price_history.append(current_price)
            if len(price_history) > 30:
                price_history.pop(0)

            print(f"Cena {SYMBOL}: {current_price} | Body v pamäti: {len(price_history)}")

            # Keď máme dostatok bodov, vyhodnotíme Fibonacciho zónu a obchodujeme
            if len(price_history) >= 20:
                max_price = max(price_history)
                min_price = min(price_history)
                diff = max_price - min_price

                if diff > 0:
                    fib_50 = max_price - (diff * FIB_MIN)
                    fib_618 = max_price - (diff * FIB_MAX)

                    positions = await connection.get_positions()
                    has_position = any(p['symbol'] == SYMBOL for p in positions)

                    if not has_position:
                        # BUY logika
                        if min_price < current_price and (min(fib_50, fib_618) <= current_price <= max(fib_50, fib_618)):
                            print(f"Cena {current_price} je v BUY Fib zóne! Vstupujem do obchodu...")
                            sl = current_price - (diff * 0.3)
                            tp = current_price + ((current_price - sl) * RISK_REWARD_RATIO)
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)
                            price_history.clear()

                        # SELL logika
                        elif max_price > current_price and (min(fib_50, fib_618) <= current_price <= max(fib_50, fib_618)):
                            print(f"Cena {current_price} je v SELL Fib zóne! Vstupujem do obchodu...")
                            sl = current_price + (diff * 0.3)
                            tp = current_price - ((sl - current_price) * RISK_REWARD_RATIO)
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp)
                            price_history.clear()

            await asyncio.sleep(20)

        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
