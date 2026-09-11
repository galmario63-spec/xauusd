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
COOLDOWN_SECONDS = 30

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

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
            send_telegram("Riobot úspešne naštartovaný!")

            while True:
                positions = await connection.get_positions()
                current_time = time.time()

                # Break-even manažment: ak zisk >= 3.0, posuň SL na openPrice + 1.0
                for pos in positions:
                    if pos['symbol'] == SYMBOL and pos['type'] == 'POSITION_TYPE_BUY':
                        open_price = pos['openPrice']
                        current_sl = pos.get('stopLoss', 0)
                        price_info = await connection.get_symbol_price(SYMBOL)
                        bid = price_info.get('bid')

                        if bid and (bid - open_price) >= 3.0:
                            target_sl = open_price + 1.0
                            if current_sl < target_sl:
                                await connection.modify_position(
                                    positionId=pos['id'],
                                    stopLoss=target_sl,
                                    takeProfit=pos.get('takeProfit', open_price + 15.0)
                                )
                                send_telegram("🔒 BE aktívne: SL posunutý na +1!")

                # Vstupná logika: SL 12, TP 15
                if len(positions) == 0 and (current_time - last_trade_time) > COOLDOWN_SECONDS:
                    price_info = await connection.get_symbol_price(SYMBOL)
                    ask = price_info.get('ask')

                    if ask:
                        sl = ask - 12.0
                        tp = ask + 15.0
                        await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stopLoss=sl, takeProfit=tp)
                        last_trade_time = current_time
                        send_telegram("🚀 Riobot otvoril obchod (TP 15, SL 12)!")

                await asyncio.sleep(5)

        except Exception as e:
            print(f"Chyba: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
