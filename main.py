import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi

SYMBOL = "BTCUSD"
LOT_SIZE = 0.30
PSAR_STEP = 0.02
PSAR_MAX = 0.20
TP_POINTS = 1500.0
SL_POINTS = 1500.0
BE_TRIGGER = 500.0
BE_LOCK = 100.0
LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
COMMENT = "Riobot M1 PSAR ONLY"

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot M1 PSAR is running"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()


def telegram(message):
    if not T_TOKEN or not T_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={"chat_id": T_CHAT, "text": message},
            timeout=10,
        )
    except Exception as exc:
        print("Telegram error:", exc, flush=True)


def calculate_psar(df):
    if len(df) < 5:
        return df

    out = df.copy().reset_index(drop=True)
    highs = out["high"].astype(float).to_numpy()
    lows = out["low"].astype(float).to_numpy()
    closes = out["close"].astype(float).to_numpy()

    psar = [0.0] * len(out)
    bull = [True] * len(out)

    is_bull = closes[1] >= closes[0]
    af = PSAR_STEP
    ep = highs[0] if is_bull else lows[0]
    psar[0] = lows[0] if is_bull else highs[0]
    bull[0] = is_bull

    for i in range(1, len(out)):
        current = psar[i - 1] + af * (ep - psar[i - 1])

        if is_bull:
            current = min(current, lows[i - 1])
            if i > 1:
                current = min(current, lows[i - 2])

            if lows[i] < current:
                is_bull = False
                current = ep
                ep = lows[i]
                af = PSAR_STEP
            elif highs[i] > ep:
                ep = highs[i]
                af = min(af + PSAR_STEP, PSAR_MAX)
        else:
            current = max(current, highs[i - 1])
            if i > 1:
                current = max(current, highs[i - 2])

            if highs[i] > current:
                is_bull = True
                current = ep
                ep = highs[i]
                af = PSAR_STEP
            elif lows[i] < ep:
                ep = lows[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

        psar[i] = current
        bull[i] = is_bull

    out["psar"] = psar
    out["bull"] = bull
    return out


async def candles(account, count=120):
    data = await account.get_historical_candles(SYMBOL, "1m", None, count)
    df = pd.DataFrame(data)

    if df.empty or len(df) < 10:
        return None

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])

    if len(df) > 2:
        df = df.iloc[:-1].copy()

    return calculate_psar(df)


def get_signal(df):
    if df is None or len(df) < 3:
        return None

    prev_bull = bool(df.iloc[-2]["bull"])
    curr_bull = bool(df.iloc[-1]["bull"])

    if not prev_bull and curr_bull:
        return "BUY"

    if prev_bull and not curr_bull:
        return "SELL"

    return None


async def symbol_info(connection):
    spec = await connection.get_symbol_specification(SYMBOL)
    digits = int(spec.get("digits", 2))
    point = float(spec.get("tickSize") or (10 ** (-digits)))
    return digits, point


async def current_price(connection, side):
    price = await connection.get_symbol_price(SYMBOL)

    if side == "BUY":
        return float(price["ask"])

    return float(price["bid"])


async def open_trade(connection, side, psar_value):
    digits, point = await symbol_info(connection)

    for attempt in range(2):
        price = await current_price(connection, side)

        if side == "BUY":
            sl = round(price - SL_POINTS * point, digits)
            tp = round(price + TP_POINTS * point, digits)
        else:
            sl = round(price + SL_POINTS * point, digits)
            tp = round(price - TP_POINTS * point, digits)

        try:
            if side == "BUY":
                await connection.create_market_buy_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    {"comment": COMMENT},
                )
            else:
                await connection.create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    {"comment": COMMENT},
                )

            msg = (
                f"{side} {SYMBOL}\n"
                f"Entry: {price:.2f}\n"
                f"SL: {sl:.2f}\n"
                f"TP: {tp:.2f}\n"
                f"M1 PSAR: {psar_value:.2f}"
            )

            print(msg, flush=True)
            telegram(msg)
            return True

        except Exception as exc:
            print(f"Order error attempt {attempt + 1}: {exc}", flush=True)

            if attempt == 0 and "invalid stops" in str(exc).lower():
                await asyncio.sleep(1)
                continue

            return False

    return False


async def manage_position(connection, position, df):
    try:
        digits, point = await symbol_info(connection)

        pos_id = position["id"]
        pos_type = position.get("type", "")
        entry = float(position.get("openPrice", 0))
        old_sl = float(position.get("stopLoss") or 0)
        tp = position.get("takeProfit")

        price_data = await connection.get_symbol_price(SYMBOL)

        bid = float(price_data["bid"])
        ask = float(price_data["ask"])

        is_buy = pos_type == "POSITION_TYPE_BUY"
        market = bid if is_buy else ask

        if is_buy:
            profit_points = (market - entry) / point
        else:
            profit_points = (entry - market) / point

        if profit_points < BE_TRIGGER:
            return

        if is_buy:
            be_sl = entry + BE_LOCK * point
        else:
            be_sl = entry - BE_LOCK * point

        be_sl = round(be_sl, digits)
        new_sl = be_sl

        psar_value = float(df.iloc[-1]["psar"])

        if is_buy and psar_value < market:
            new_sl = max(new_sl, round(psar_value, digits))

        elif not is_buy and psar_value > market:
            new_sl = min(new_sl, round(psar_value, digits))

        if is_buy:
            improve = old_sl == 0 or new_sl > old_sl
        else:
            improve = old_sl == 0 or new_sl < old_sl

        if not improve:
            return

        try:
            await connection.modify_position(pos_id, new_sl, tp)
            print(f"SL moved: {pos_id} -> {new_sl}", flush=True)

        except Exception as exc:
            print("SL modify skipped:", exc, flush=True)

    except Exception as exc:
        print("Position management error:", exc, flush=True)


async def trading_session(api):
    account = await api.metatrader_account_api.get_account(M_ACC)

    if account.state != "DEPLOYED":
        print("Deploying MetaApi account...", flush=True)
        await account.deploy()

    connection = account.get_rpc_connection()

    await connection.connect()

    print("Waiting for MetaApi synchronization...", flush=True)

    await connection.wait_synchronized()

    print("MetaApi synchronized", flush=True)

    telegram(
        "RIObot START\n"
        f"{SYMBOL} lot {LOT_SIZE}\n"
        "M1 PSAR ONLY\n"
        "EMA OFF | 5M FILTER OFF\n"
        f"TP {TP_POINTS} | SL {SL_POINTS}\n"
        f"BE +{BE_TRIGGER} -> +{BE_LOCK}"
    )

    last_candle = None

    try:
        while True:
            df = await candles(account)

            if df is None or df.empty:
                await asyncio.sleep(LOOP_SECONDS)
                continue

            positions = await connection.get_positions()

            symbol_positions = [
                p for p in positions if p.get("symbol") == SYMBOL
            ]

            for position in symbol_positions:
                await manage_position(connection, position, df)

            candle_id = str(df.iloc[-1].get("time", len(df)))

            if candle_id != last_candle:
                last_candle = candle_id

                signal = get_signal(df)
                psar_value = float(df.iloc[-1]["psar"])
                close_value = float(df.iloc[-1]["close"])

                print(
                    f"M1 closed | close={close_value:.2f} | "
                    f"PSAR={psar_value:.2f} | signal={signal}",
                    flush=True,
                )

                if not symbol_positions and signal:
                    await open_trade(
                        connection,
                        signal,
                        psar_value,
                    )

            await asyncio.sleep(LOOP_SECONDS)

    finally:
        try:
            await connection.close()
        except Exception:
            pass


async def main():
    if not M_TOKEN or not M_ACC:
        raise RuntimeError(
            "Missing M_TOKEN or M_ACC environment variable"
        )

    api = MetaApi(M_TOKEN)

    while True:
        try:
            await trading_session(api)

        except Exception as exc:
            print("BOT ERROR:", exc, flush=True)

            telegram(
                f"RIObot connection error: {exc}\n"
                f"Reconnect in {RECONNECT_SECONDS}s"
            )

            await asyncio.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
