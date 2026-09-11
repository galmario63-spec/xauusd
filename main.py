import os
import time
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests

app = Flask('')

@app.route('/')
def home():
    return "Riobot XAUUSD Safe Engine"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = "XAUUSD"
LOT_SIZE = 0.01

last_trade_time = 0
COOLDOWN_SECONDS = 30  # Znížené na 30 sekúnd pre bleskovú reakciu

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def calculate_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_macd(prices):
    if len(prices) < 26:
        return 0, 0
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    return ema12 - ema26, 0

def calculate_stochastic(highs, lows, closes, period=14):
    if len(closes) < period:
        return 50
    lowest_low = min(lows[-period:])
    highest_high = max(highs[-period:])
    if highest_high == lowest_low:
        return 50
    return 100 * ((closes[-1] - lowest_low) / (highest_high - lowest_low))

async def run_bot():
    global last_trade_time
    while True:
        try:
            api = MetaApi(METAAPI_TOKEN)
            account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
            await account.wait_connected()
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
            send_telegram("Riobot úspešne naštartovaný a pripravený!")

            while True:
                positions = await connection.get_positions()
                current_time = time.time()

                # Vstupná logika - uvoľnené podmienky pre rýchlejšie obchody
                if len(positions) == 0 and (current_time - last_trade_time) > COOLDOWN_SECONDS:
                    price_info = await connection.get_symbol_price(SYMBOL)
                    bid = price_info.get('bid')
                    ask = price_info.get('ask')

                    if ask:
                        # Okamžitý nákupný signál pre test / rozbehnutie
                        print("Otváram pozíciu na XAUUSD...")
                        await connection.create_market_buy_order(SYMBOL, LOT_SIZE)
                        last_trade_time = current_time
                        send_telegram("🚀 Riobot otvoril nový obchod na XAUUSD!")

                await asyncio.sleep(10)

        except Exception as e:
            print(f"Chyba v bote: {e}")
            send_telegram(f"⚠️ Riobot hlási chybu: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
