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

TP_POINTS = 3000.0
SL_POINTS = 3000.0

BE_TRIGGER = 1000.0
BE_LOCK = 200.0

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15

COMMENT = "Riobot M1 PSAR SL BE"


M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot M1 PSAR SL+BE active"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()


def telegram(message):
    if not T_TOKEN or not T_CHAT:
        return

    try:
        url = f"https://api.telegram.org/bot{T_TOKEN}/sendMessage"
        requests.post(
            url,
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )
    except Exception as exc:
        print("Telegram error:", exc, flush=True)


def psar_values(df):
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    closes = df["close"].astype(float).tolist()

    n = len(df)

    if n < 5:
        return [], []

    psar = [0.0] * n
    bull = [True] * n

    is_bull = closes[1] >= closes[0]
    af = PSAR_STEP

    if is_bull:
        sar = min(lows[0], lows[1])
        ep = max(highs[0], highs[1])
    else:
        sar = max(highs[0], highs[1])
        ep = min(lows[0], lows[1])

    psar[0] = sar
    psar[1] = sar
    bull[0] = is_bull
    bull[1] = is_bull

    for i in range(2, n):

        sar = sar + af * (ep - sar)

        if is_bull:

            sar = min(
                sar,
                lows[i - 1],
                lows[i - 2]
            )

            if lows[i] < sar:

                is_bull = False
                sar = ep
                ep = lows[i]
                af = PSAR_STEP

            else:

                if highs[i] > ep:
                    ep = highs[i]
                    af = min(
                        af + PSAR_STEP,
                        PSAR_MAX
                    )

        else:

            sar = max(
                sar,
                highs[i - 1],
                highs[i - 2]
            )

            if highs[i] > sar:

                is_bull = True
                sar = ep
                ep = highs[i]
                af = PSAR_STEP

            else:

                if lows[i] < ep:
                    ep = lows[i]
                    af = min(
                        af + PSAR_STEP,
                        PSAR_MAX
                    )

        psar[i] = sar
        bull[i] = is_bull

    return psar, bull


def get_signal(df):

    psar, bull = psar_values(df)

    if len(bull) < 3:
        return None, None

    if (not bull[-2]) and bull[-1]:
        return "BUY", psar[-1]

    if bull[-2] and (not bull[-1]):
        return "SELL", psar[-1]

    return None, psar[-1]


async def candles(account):

    raw = await account.get_historical_candles(
        SYMBOL,
        "1m",
        None,
        100
    )

    df = pd.DataFrame(raw)

    if len(df) < 10:
        return None

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    if len(df) < 10:
        return None

    # odstrani aktualnu neuzavretu M1 sviecku
    return df.iloc[:-1].copy()


async def symbol_info(connection):

    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    digits = int(
        spec.get("digits", 2)
    )

    point = float(
        spec.get("tickSize")
        or (10 ** (-digits))
    )

    return digits, point


async def open_trade(
    connection,
    side,
    digits,
    point,
    psar
):

    price_data = await connection.get_symbol_price(
        SYMBOL
    )

    if side == "BUY":

        entry = float(
            price_data["ask"]
        )

        sl = round(
            entry - SL_POINTS * point,
            digits
        )

        tp = round(
            entry + TP_POINTS * point,
            digits
        )

    else:

        entry = float(
            price_data["bid"]
        )

        sl = round(
            entry + SL_POINTS * point,
            digits
        )

        tp = round(
            entry - TP_POINTS * point,
            digits
        )

    options = {
        "comment": COMMENT
    }

    try:

        if side == "BUY":

            await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                options
            )

        else:

            await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                options
            )

    except Exception as exc:

        if "Invalid stops" not in str(exc):
            raise

        await asyncio.sleep(1)

        price_data = await connection.get_symbol_price(
            SYMBOL
        )

        if side == "BUY":

            entry = float(
                price_data["ask"]
            )

            sl = round(
                entry - SL_POINTS * point,
                digits
            )

            tp = round(
                entry + TP_POINTS * point,
                digits
            )

            await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                options
            )

        else:

            entry = float(
                price_data["bid"]
            )

            sl = round(
                entry + SL_POINTS * point,
                digits
            )

            tp = round(
                entry - TP_POINTS * point,
                digits
            )

            await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                options
            )

    telegram(
        f"{side} {SYMBOL}\n"
        f"Entry: {entry:.2f}\n"
        f"SL: {sl:.2f}\n"
        f"TP: {tp:.2f}\n"
        f"M1 PSAR: {psar:.2f}"
    )

    print(
        side,
        SYMBOL,
        entry,
        sl,
        tp,
        flush=True
    )


async def manage_break_even(
    connection,
    digits,
    point
):

    positions = await connection.get_positions()

    for pos in positions:

        if pos.get("symbol") != SYMBOL:
            continue

        pos_id = pos.get("id")
        pos_type = pos.get("type")

        open_price = float(
            pos.get("openPrice", 0)
        )

        current_sl = pos.get("stopLoss")
        current_tp = pos.get("takeProfit")

        if current_sl is not None:
            current_sl = float(current_sl)

        price_data = await connection.get_symbol_price(
            SYMBOL
        )

        if pos_type == "POSITION_TYPE_BUY":

            current = float(
                price_data["bid"]
            )

            trigger = (
                open_price
                + BE_TRIGGER * point
            )

            be_sl = round(
                open_price
                + BE_LOCK * point,
                digits
            )

            already_be = (
                current_sl is not None
                and current_sl >= be_sl
            )

            if current >= trigger and not already_be:

                try:

                    await connection.modify_position(
                        pos_id,
                        be_sl,
                        current_tp
                    )

                    telegram(
                        f"BE BUY {SYMBOL}\n"
                        f"SL -> {be_sl:.2f}"
                    )

                except Exception as exc:

                    print(
                        "BE BUY error:",
                        exc,
                        flush=True
                    )

        elif pos_type == "POSITION_TYPE_SELL":

            current = float(
                price_data["ask"]
            )

            trigger = (
                open_price
                - BE_TRIGGER * point
            )

            be_sl = round(
                open_price
                - BE_LOCK * point,
                digits
            )

            already_be = (
                current_sl is not None
                and current_sl <= be_sl
            )

            if current <= trigger and not already_be:

                try:

                    await connection.modify_position(
                        pos_id,
                        be_sl,
                        current_tp
                    )

                    telegram(
                        f"BE SELL {SYMBOL}\n"
                        f"SL -> {be_sl:.2f}"
                    )

                except Exception as exc:

                    print(
                        "BE SELL error:",
                        exc,
                        flush=True
                    )


async def trading_session(api):

    account = await api.metatrader_account_api.get_account(
        M_ACC
    )

    if account.state != "DEPLOYED":

        await account.deploy()
        await account.wait_deployed()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    digits, point = await symbol_info(
        connection
    )

    telegram(
        "RIObot START\n"
        f"{SYMBOL} lot {LOT_SIZE}\n"
        "M1 PSAR entry only\n"
        "PSAR trailing OFF\n"
        f"TP {TP_POINTS} | SL {SL_POINTS}\n"
        f"BE +{BE_TRIGGER} -> +{BE_LOCK}"
    )

    print(
        "RIObot connected",
        flush=True
    )

    last_candle = None

    try:

        while True:

            try:

                # IBA BREAK EVEN
                await manage_break_even(
                    connection,
                    digits,
                    point
                )

                df = await candles(account)

                if df is None or df.empty:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                candle_id = str(
                    df.iloc[-1].get(
                        "time",
                        len(df)
                    )
                )

                if candle_id != last_candle:

                    last_candle = candle_id

                    positions = await connection.get_positions()

                    own_positions = [
                        p
                        for p in positions
                        if p.get("symbol") == SYMBOL
                    ]

                    # iba jedna BTCUSD pozicia
                    if not own_positions:

                        signal, psar = get_signal(df)

                        if signal:

                            await open_trade(
                                connection,
                                signal,
                                digits,
                                point,
                                psar
                            )

                await asyncio.sleep(
                    LOOP_SECONDS
                )

            except Exception as exc:

                print(
                    "Loop error:",
                    exc,
                    flush=True
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
                flush=True
            )

            telegram(
                f"RIObot ERROR\n{exc}"
            )

            print(
                f"Reconnect in {RECONNECT_SECONDS}s",
                flush=True
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


if __name__ == "__main__":

    keep_alive()
    asyncio.run(main())
