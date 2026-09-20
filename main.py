import os
import asyncio
from flask import Flask
from threading import Thread
requests_lib = __import__('requests')

app = Flask(__name__)

@app.route("/")
def home():
    return "Riobot PSAR Active"

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
COMMENT = "Riobot PSAR"

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
                send_telegram(f"🚀 RIObot PSAR ŠTART\nSymbol: {symbol}")
                startup_message_sent = True

            while True:
                try:
                    price = await connection.get_symbol_price(symbol)
                    bid, ask = float(price["bid"]), float(price["ask"])

                    candles = await connection.get_historical_candles(symbol, "1m", 50)
                    if not candles or len(candles) < 10:
                        await asyncio.sleep(5)
                        continue

                    highs = [c["high"] for c in candles]
                    lows = [c["low"] for c in candles]
                    closes = [c["close"] for c in candles]

                    # Parabolic SAR výpočet
                    psar = lows[0]
                    af = 0.02
                    max_af = 0.20
                    ep = highs[0]
                    trend = 1

                    for i in range(1, len(closes)):
                        prev_psar = psar
                        if trend == 1:
                            psar = prev_psar + af * (ep - prev_psar)
                            psar = min(psar, lows[i-1], lows[max(0, i-2)])
                            if lows[i] < psar:
                                trend = -1
                                psar = ep
                                ep = lows[i]
                                af = 0.02
                            else:
                                if highs[i] > ep:
                                    ep = highs[i]
                                    af = min(af + 0.02, max_af)
                        else:
                            psar = prev_psar - af * (prev_psar - ep)
                            psar = max(psar, highs[i-1], highs[max(0, i-2)])
                            if highs[i] > psar:
                                trend = 1
                                psar = ep
                                ep = highs[i]
                                af = 0.02
                            else:
                                if lows[i] < ep:
                                    ep = lows[i]
                                    af = min(af + 0.02, max_af)

                    positions = await connection.get_positions()
                    bot_positions = [p for p in positions if p.get("symbol") == symbol and is_bot_position(p)]

                    if not bot_positions:
                        if trend == 1:
                            sl = round(ask - SL_POINTS * point, digits)
                            tp = round(ask + TP_POINTS * point, digits)
                            await connection.create_market_buy_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                options={"stopLoss": sl, "takeProfit": tp, "comment": COMMENT, "magic": MAGIC}
                            )
                            send_telegram(f"🟢 PSAR BUY OTVORENÝ\nCena: {ask}\nSL: {sl} | TP: {tp}")
                        elif trend == -1:
                            sl = round(bid + SL_POINTS * point, digits)
                            tp = round(bid - TP_POINTS * point, digits)
                            await connection.create_market_sell_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                options={"stopLoss": sl, "takeProfit": tp, "comment": COMMENT, "magic": MAGIC}
                            )
                            send_telegram(f"🔴 PSAR SELL OTVORENÝ\nCena: {bid}\nSL: {sl} | TP: {tp}")

                    await asyncio.sleep(15)

                except Exception as inner_error:
                    print(f"Chyba v cykle: {inner_error}")
                    await asyncio.sleep(5)

        except Exception as outer_error:
            print(f"Chyba pripojenia: {outer_error}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
