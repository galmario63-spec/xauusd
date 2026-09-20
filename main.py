import os
import asyncio
from flask import Flask
from threading import Thread
requests_lib = __import__('requests')
import time

app = Flask(__name__)

@app.route("/")
def home():
    return "Riobot Instant Active"

def run_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_server, daemon=True)
    t.start()

TELEGRAM_TOKEN = os.getenv("T_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("T_CHAT")
METAAPI_TOKEN = os.getenv("M_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("M_ACC")

SYMBOL_REQUEST = "BTCUSD"
LOT_SIZE = 0.30
TP_POINTS = 600.0
SL_POINTS = 1500.0
MAGIC = 26092026
COMMENT = "Riobot Instant"

startup_message_sent = False

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests_lib.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def is_bot_position(position):
    try:
        if int(position.get("magic", 0)) == MAGIC:
            return True
    except Exception:
        pass
    return position.get("comment") == COMMENT

async def main():
    global startup_message_sent
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        return

    from metaapi_cloud_sdk import MetaApi
    api = MetaApi(METAAPI_TOKEN)

    while True:
        try:
            account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
            if account.state != "DEPLOYED":
                await account.deploy()
            
            await account.wait_connected()
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()

            symbol = SYMBOL_REQUEST
            specification = await connection.get_symbol_specification(symbol)
            point = float(specification.get("point", 0.01))
            digits = int(specification.get("digits", 2))

            if not startup_message_sent:
                send_telegram(f"🚀 RIObot INSTANTNÝ ŠTART\nSymbol: {symbol}")
                startup_message_sent = True

            while True:
                try:
                    price = await connection.get_symbol_price(symbol)
                    bid, ask = float(price["bid"]), float(price["ask"])

                    positions = await connection.get_positions()
                    bot_positions = [p for p in positions if p.get("symbol") == symbol and is_bot_position(p)]

                    if not bot_positions:
                        sl = round(ask - SL_POINTS * point, digits)
                        tp = round(ask + TP_POINTS * point, digits)
                        await connection.create_market_buy_order(
                            symbol=symbol,
                            volume=LOT_SIZE,
                            options={"stopLoss": sl, "takeProfit": tp, "comment": COMMENT, "magic": MAGIC}
                        )
                        send_telegram(f"🟢 OKAMŽITÝ BUY OTVORENÝ!\nCena: {ask}\nSL: {sl} | TP: {tp}")

                    await asyncio.sleep(10)

                except Exception as inner_error:
                    print(f"Chyba v cykle: {inner_error}")
                    send_telegram(f"⚠️ Chyba obchodu: {str(inner_error)}")
                    await asyncio.sleep(5)
                    break

        except Exception as outer_error:
            print(f"Chyba pripojenia: {outer_error}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
