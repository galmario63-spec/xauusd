import os
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests

app = Flask(__name__)

@app.route("/")
def home():
    return "Riobot PSAR M1 Active"

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
PSAR_STEP = 0.80
PSAR_MAXIMUM = 0.40
LOT_SIZE = 0.30
TP_POINTS = 600.0
SL_POINTS = 1500.0
BE_TRIGGER = 250.0
BE_LOCK = 100.0
MAGIC = 26092026
COMMENT = "Riobot PSAR M1"

startup_message_sent = False

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def calculate_psar(candles, step, maximum):
    if len(candles) < 3:
        return [], []
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    sar = [None] * len(candles)
    direction = [None] * len(candles)
    uptrend = closes[1] >= closes[0]
    sar[0] = lows[0] if uptrend else highs[0]
    extreme_point = highs[0] if uptrend else lows[0]
    acceleration = step
    direction[0] = uptrend

    for i in range(1, len(candles)):
        previous_sar = sar[i - 1]
        current_sar = previous_sar + acceleration * (extreme_point - previous_sar)
        if uptrend:
            current_sar = min(current_sar, lows[i - 1], lows[i - 2] if i >= 2 else lows[i - 1])
            if lows[i] < current_sar:
                uptrend = False
                current_sar = extreme_point
                extreme_point = lows[i]
                acceleration = step
            else:
                if highs[i] > extreme_point:
                    extreme_point = highs[i]
                    acceleration = min(maximum, acceleration + step)
        else:
            current_sar = max(current_sar, highs[i - 1], highs[i - 2] if i >= 2 else highs[i - 1])
            if highs[i] > current_sar:
                uptrend = True
                current_sar = extreme_point
                extreme_point = highs[i]
                acceleration = step
            else:
                if lows[i] < extreme_point:
                    extreme_point = lows[i]
                    acceleration = min(maximum, acceleration + step)
        sar[i] = current_sar
        direction[i] = uptrend
    return sar, direction

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
            specification = None
            for _ in range(5):
                try:
                    specification = await connection.get_symbol_specification(symbol)
                    if specification:
                        break
                except Exception:
                    await asyncio.sleep(2)

            if not specification:
                await asyncio.sleep(5)
                continue

            point = float(specification.get("point", 0.01))
            digits = int(specification.get("digits", 2))

            if not startup_message_sent:
                send_telegram(f"🚀 RIObot LIVE FIX SPUSTENÝ\nSymbol: {symbol} | Lot: {LOT_SIZE}")
                startup_message_sent = True

            while True:
                try:
                    price = await connection.get_symbol_price(symbol)
                    bid, ask = float(price["bid"]), float(price["ask"])

                    candles = await connection.get_historical_candles(symbol, TIMEFRAME, None, 150)
                    if not candles or len(candles) < 10:
                        await asyncio.sleep(3)
                        continue

                    candles = sorted(candles, key=lambda x: x["time"])
                    sar_values, directions = calculate_psar(candles, PSAR_STEP, PSAR_MAXIMUM)
                    if not sar_values:
                        await asyncio.sleep(2)
                        continue

                    current_direction = directions[-1]

                    positions = await connection.get_positions()
                    bot_positions = [p for p in positions if p.get("symbol") == symbol and is_bot_position(p)]

                    for pos in bot_positions:
                        open_price = float(pos["openPrice"])
                        current_sl = float(pos.get("stopLoss") or 0)
                        current_tp = pos.get("takeProfit")

                        if pos["type"] == "POSITION_TYPE_BUY":
                            if (bid - open_price) / point >= BE_TRIGGER:
                                target_sl = round(open_price + BE_LOCK * point, digits)
                                if current_sl == 0 or current_sl < target_sl:
                                    try:
                                        await connection.modify_position(positionId=pos["id"], stop_loss=target_sl, take_profit=current_tp)
                                    except Exception:
                                        pass
                        elif pos["type"] == "POSITION_TYPE_SELL":
                            if (open_price - ask) / point >= BE_TRIGGER:
                                target_sl = round(open_price - BE_LOCK * point, digits)
                                if current_sl == 0 or current_sl > target_sl:
                                    try:
                                        await connection.modify_position(positionId=pos["id"], stop_loss=target_sl, take_profit=current_tp)
                                    except Exception:
                                        pass

                    if not bot_positions:
                        if current_direction is True:
                            sl = round(ask - SL_POINTS * point, digits)
                            tp = round(ask + TP_POINTS * point, digits)
                            try:
                                await connection.create_market_buy_order(
                                    symbol, LOT_SIZE, stop_loss=sl, take_profit=tp,
                                    options={"comment": COMMENT, "magic": MAGIC}
                                )
                                send_telegram(f"🟢 LIVE BUY OTVORENÝ\n{symbol}\nSL: {sl} | TP: {tp}")
                            except Exception as e:
                                send_telegram(f"❌ BUY ERROR: {e}")

                        elif current_direction is False:
                            sl = round(bid + SL_POINTS * point, digits)
                            tp = round(bid - TP_POINTS * point, digits)
                            try:
                                await connection.create_market_sell_order(
                                    symbol, LOT_SIZE, stop_loss=sl, take_profit=tp,
                                    options={"comment": COMMENT, "magic": MAGIC}
                                )
                                send_telegram(f"🔴 LIVE SELL OTVORENÝ\n{symbol}\nSL: {sl} | TP: {tp}")
                            except Exception as e:
                                send_telegram(f"❌ SELL ERROR: {e}")

                    await asyncio.sleep(2)

                except Exception as inner_error:
                    print(f"Chyba: {inner_error}")
                    await asyncio.sleep(5)
                    break

        except Exception as outer_error:
            print(f"Pripojenie: {outer_error}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
