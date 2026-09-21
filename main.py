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
COMMENT = "Riobot 5M+1M PSAR"

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot running"


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


def psar(df):
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    if len(df) < 5:
        raise ValueError("Not enough candles for PSAR")

    values = [0.0] * len(df)
    bull = close[1] >= close[0]
    af = PSAR_STEP
    ep = high[0] if bull else low[0]
    values[0] = low[0] if bull else high[0]

    for i in range(1, len(df)):
        values[i] = values[i - 1] + af * (ep - values[i - 1])

        if bull:
            values[i] = min(values[i], low[i - 1])
            if i >= 2:
                values[i] = min(values[i], low[i - 2])

            if low[i] < values[i]:
                bull = False
                values[i] = ep
                ep = low[i]
                af = PSAR_STEP
            elif high[i] > ep:
                ep = high[i]
                af = min(af + PSAR_STEP, PSAR_MAX)
        else:
            values[i] = max(values[i], high[i - 1])
            if i >= 2:
                values[i] = max(values[i], high[i - 2])

            if high[i] > values[i]:
                bull = True
                values[i] = ep
                ep = high[i]
                af = PSAR_STEP
            elif low[i] < ep:
                ep = low[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

    return values[-1], bull


async def get_candles(account, timeframe):
    data = await account.get_historical_candles(
        SYMBOL,
        timeframe,
        None,
        100,
    )

    if not data or len(data) < 12:
        raise RuntimeError(
            f"Not enough {timeframe} candles"
        )

    df = pd.DataFrame(data)
    df = df.sort_values("time")
    df = df.reset_index(drop=True)

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    # Remove currently forming candle
    return df.iloc[:-1].copy()


async def get_positions(connection):
    positions = await connection.get_positions()

    return [
        p
        for p in positions
        if p.get("symbol") == SYMBOL
    ]


async def get_point_digits(connection):
    specification = (
        await connection.get_symbol_specification(
            SYMBOL
        )
    )

    if not specification:
        raise RuntimeError(
            f"Symbol {SYMBOL} not found"
        )

    digits = int(
        specification.get("digits", 2)
    )

    point = float(
        specification.get("tickSize")
        or (10 ** -digits)
    )

    return point, digits


async def protect_position(connection):
    open_positions = await get_positions(
        connection
    )

    if not open_positions:
        return

    point, digits = await get_point_digits(
        connection
    )

    price = await connection.get_symbol_price(
        SYMBOL
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in open_positions:
        open_price = float(
            position["openPrice"]
        )

        old_sl = float(
            position.get("stopLoss") or 0
        )

        take_profit = position.get(
            "takeProfit"
        )

        position_type = position.get(
            "type"
        )

        try:
            if position_type == "POSITION_TYPE_BUY":
                profit_points = (
                    bid - open_price
                ) / point

                if profit_points >= BE_TRIGGER:
                    new_sl = round(
                        open_price
                        + BE_LOCK * point,
                        digits,
                    )

                    if old_sl == 0 or new_sl > old_sl:
                        await connection.modify_position(
                            position["id"],
                            new_sl,
                            take_profit,
                        )

            elif position_type == "POSITION_TYPE_SELL":
                profit_points = (
                    open_price - ask
                ) / point

                if profit_points >= BE_TRIGGER:
                    new_sl = round(
                        open_price
                        - BE_LOCK * point,
                        digits,
                    )

                    if old_sl == 0 or new_sl < old_sl:
                        await connection.modify_position(
                            position["id"],
                            new_sl,
                            take_profit,
                        )

        except Exception as exc:
            print(
                "BE modify error:",
                exc,
                flush=True,
            )


async def open_order(
    connection,
    side,
    psar_value,
):
    point, digits = await get_point_digits(
        connection
    )

    price = await connection.get_symbol_price(
        SYMBOL
    )

    ask = float(price["ask"])
    bid = float(price["bid"])

    entry = ask if side == "BUY" else bid

    if side == "BUY":
        sl = round(
            entry - SL_POINTS * point,
            digits,
        )

        tp = round(
            entry + TP_POINTS * point,
            digits,
        )

    else:
        sl = round(
            entry + SL_POINTS * point,
            digits,
        )

        tp = round(
            entry - TP_POINTS * point,
            digits,
        )

    options = {
        "comment": COMMENT
    }

    if side == "BUY":
        result = (
            await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                options,
            )
        )

    else:
        result = (
            await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                options,
            )
        )

    print(
        "ORDER:",
        result,
        flush=True,
    )

    telegram(
        f"{side} {SYMBOL}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        f"PSAR: {psar_value:.2f}"
    )


async def trading_session(api):
    account = (
        await api.metatrader_account_api.get_account(
            M_ACC
        )
    )

    if account.state != "DEPLOYED":
        await account.deploy()
        await account.wait_deployed()

    connection = (
        account.get_rpc_connection()
    )

    await connection.connect()
    await connection.wait_synchronized()

    print(
        "RIObot connected",
        flush=True,
    )

    telegram(
        f"RIObot START\n"
        f"{SYMBOL} lot {LOT_SIZE}\n"
        "5M PSAR direction + 1M PSAR flip"
    )

    last_seen_candle = None

    try:
        while True:
            await protect_position(
                connection
            )

            df5 = await get_candles(
                account,
                "5m",
            )

            _, bull5 = psar(df5)

            df1 = await get_candles(
                account,
                "1m",
            )

            _, previous_bull = psar(
                df1.iloc[:-1]
            )

            current_psar, current_bull = (
                psar(df1)
            )

            signal = None

            if (
                bull5
                and not previous_bull
                and current_bull
            ):
                signal = "BUY"

            elif (
                not bull5
                and previous_bull
                and not current_bull
            ):
                signal = "SELL"

            candle_id = str(
                df1.iloc[-1]["time"]
            )

            open_positions = (
                await get_positions(
                    connection
                )
            )

            direction5 = (
                "BUY"
                if bull5
                else "SELL"
            )

            direction1 = (
                "BUY"
                if current_bull
                else "SELL"
            )

            print(
                f"{SYMBOL} "
                f"5M={direction5} "
                f"1M={direction1} "
                f"SIGNAL={signal}",
                flush=True,
            )

            if candle_id != last_seen_candle:
                last_seen_candle = candle_id

                if signal and not open_positions:
                    try:
                        await open_order(
                            connection,
                            signal,
                            current_psar,
                        )

                    except Exception as exc:
                        print(
                            "Order error:",
                            exc,
                            flush=True,
                        )

            await asyncio.sleep(
                LOOP_SECONDS
            )

    finally:
        try:
            await connection.close()
        except Exception:
            pass


async def main():
    if not M_TOKEN or not M_ACC:
        raise RuntimeError(
            "Missing M_TOKEN or M_ACC"
        )

    api = MetaApi(M_TOKEN)

    while True:
        try:
            await trading_session(api)

        except Exception as exc:
            print(
                "BOT ERROR:",
                exc,
                flush=True,
            )

            print(
                f"Reconnect in "
                f"{RECONNECT_SECONDS}s",
                flush=True,
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
