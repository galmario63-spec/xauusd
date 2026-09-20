import os
import asyncio
from flask import Flask
from threading import Thread
requests_lib = __import__('requests')
import time

app = Flask(__name__)

@app.route("/")
def home():
    return "Riobot Simple Mode Active"

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
TIMEFRAME = "1m"
LOT_SIZE = 0.30
TP_POINTS = 600.0
SL_POINTS = 1500.0
BE_TRIGGER = 250.0
BE_LOCK = 100.0
MAGIC = 26092026
COMMENT = "Riobot Simple"

startup_message_sent = False
last_status_time = 0

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests_lib.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_rest_candles(account_id, token, symbol):
    try:
        url = f"https://mt-client-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts/{account_id}/historical-candles/{symbol}/1m"
        headers = {"auth-token": token}
        response = requests_lib.get(url, headers=headers, params={"limit": 10}, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"REST candles error: {e}")
    return []

def is_bot_position(position):
    try:
        if int(position.get("magic", 0)) == MAGIC:
            return True
    except Exception:
        pass
    return position.get("comment") == COMMENT

async def main():
    global startup_message_sent, last_status_time
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
                send_telegram(f"🚀 RIObot JEDNODUCHÝ REŽIM ŠTART\nSymbol: {symbol}")
                startup_message_sent = True

            while True:
                try:
                    price = await connection.get_symbol_price(symbol)
                    bid, ask = float(price["bid"]), float(price["ask"])

                    candles = get_rest_candles(METAAPI_ACCOUNT_ID, METAAPI_TOKEN, symbol)
                    if not candles or len(candles) < 2:
                        await asyncio.sleep(3)
                        continue

                    last_candle = candles[-1]
                    is_bullish = float(last_candle["close"]) > float(last_candle["open"])

                    positions = await connection.get_positions()
                    bot_positions = [p for p in positions if p.get("symbol") == symbol and is_bot_position(p)]

                    current_time = time.time()
                    if current_time - last_status_time > 60:
                        send_telegram(f"📊 BOT STATUS:\nCena: {ask}\nPozície: {len(bot_positions)}")
                        last_status_time = current_time

                    if not bot_positions:
                        if is_bullish:
                            sl = round(ask - SL_POINTS * point, digits)
                            tp = round(ask + TP_POINTS * point, digits)
                            await connection.create_market_buy_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                options={"stopLoss": sl, "takeProfit": tp, "comment": COMMENT, "magic": MAGIC}
                            )
                            send_telegram(f"🟢 BUY OTVORENÝ!\nCena: {ask}")
                        else:
                            sl = round(bid + SL_POINTS * point, digits)
                            tp = round(bid - TP_POINTS * point, digits)
                            await connection.create_market_sell_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                options={"stopLoss": sl, "takeProfit": tp, "comment": COMMENT, "magic": MAGIC}
                            )
                            send_telegram(f"🔴 SELL OTVORENÝ!\nCena: {bid}")

                    await asyncio.sleep(5)

                except Exception as inner_error:
                    print(f"Chyba v cykle: {inner_error}")
                    await asyncio.sleep(5)
                    break

        except Exception as outer_error:
            print(f"Chyba pripojenia: {outer_error}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
