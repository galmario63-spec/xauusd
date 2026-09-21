import asyncio
import os
import traceback
from flask import Flask
from threading import Thread

import pandas as pd
import requests
from metaapi_cloud_sdk import MetaApi


# =========================================================
# NASTAVENIA
# =========================================================

SYMBOL = "BTCUSD"

# CENTOVY UCET
LOT_SIZE = 0.30

# PARABOLIC SAR
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# TP / SL 1:1
TP_POINTS = 1500.0
SL_POINTS = 1500.0

# BREAK EVEN
BE_TRIGGER = 500.0
BE_LOCK = 100.0

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15

COMMENT = "Riobot M1 PSAR ONLY"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK / RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot M1 PSAR ONLY is running"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port
    )


def keep_alive():
    Thread(
        target=run_server,
        daemon=True
    ).start()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):

    if not T_TOKEN or not T_CHAT:
        print(message)
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )

    except Exception as e:
        print("Telegram error:", e)


# =========================================================
# PARABOLIC SAR
# =========================================================

def calculate_psar(df):

    if len(df) < 5:
        raise ValueError("Too few candles for PSAR")

    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    psar = [0.0] * len(df)

    bull = close[1] >= close[0]

    af = PSAR_STEP

    if bull:
        ep = high[0]
        psar[0] = low[0]

    else:
        ep = low[0]
        psar[0] = high[0]

    for i in range(1, len(df)):

        psar[i] = (
            psar[i - 1]
            + af * (ep - psar[i - 1])
        )

        if bull:

            if i >= 2:
                psar[i] = min(
                    psar[i],
                    low[i - 1],
                    low[i - 2]
                )

            else:
                psar[i] = min(
                    psar[i],
                    low[i - 1]
                )

            if low[i] < psar[i]:

                bull = False

                psar[i] = ep

                ep = low[i]

                af = PSAR_STEP

            elif high[i] > ep:

                ep = high[i]

                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        else:

            if i >= 2:
                psar[i] = max(
                    psar[i],
                    high[i - 1],
                    high[i - 2]
                )

            else:
                psar[i] = max(
                    psar[i],
                    high[i - 1]
                )

            if high[i] > psar[i]:

                bull = True

                psar[i] = ep

                ep = high[i]

                af = PSAR_STEP

            elif low[i] < ep:

                ep = low[i]

                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

    return float(psar[-1]), bull


# =========================================================
# M1 SVIECKY
# =========================================================

async def get_closed_candles(account):

    candles = await account.get_historical_candles(
        SYMBOL,
        "1m",
        None,
        100
    )

    if not candles:
        raise RuntimeError("No M1 candle data")

    df = pd.DataFrame(candles)

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    for col in (
        "open",
        "high",
        "low",
        "close"
    ):

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    )

    # Odstranime aktualnu rozpracovanu sviecku
    df = df.iloc[:-1].copy()

    if len(df) < 10:
        raise RuntimeError(
            "Too few closed M1 candles"
        )

    return df


# =========================================================
# SYMBOL
# =========================================================

async def symbol_data(connection):

    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    if not spec:
        raise RuntimeError(
            f"Symbol {SYMBOL} not found"
        )

    point = float(
        spec.get("tickSize") or 0.01
    )

    digits = int(
        spec.get("digits", 2)
    )

    return point, digits


# =========================================================
# POZICIE
# =========================================================

async def positions(connection):

    all_positions = await connection.get_positions()

    return [
        p for p in all_positions
        if p.get("symbol") == SYMBOL
    ]


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_break_even(connection):

    pos = await positions(connection)

    if not pos:
        return

    point, digits = await symbol_data(
        connection
    )

    price = await connection.get_symbol_price(
        SYMBOL
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for p in pos:

        side = p["type"]

        open_price = float(
            p["openPrice"]
        )

        current_sl = float(
            p.get("stopLoss") or 0
        )

        tp = p.get("takeProfit")

        # BUY
        if side == "POSITION_TYPE_BUY":

            profit = (
                bid - open_price
            ) / point

            new_sl = round(
                open_price
                + BE_LOCK * point,
                digits
            )

            if (
                profit >= BE_TRIGGER
                and (
                    current_sl == 0
                    or current_sl < new_sl
                )
            ):

                await connection.modify_position(
                    p["id"],
                    new_sl,
                    tp
                )

                telegram(
                    f"BREAK EVEN BUY {SYMBOL}\n"
                    f"SL -> {new_sl}"
                )

        # SELL
        elif side == "POSITION_TYPE_SELL":

            profit = (
                open_price - ask
            ) / point

            new_sl = round(
                open_price
                - BE_LOCK * point,
                digits
            )

            if (
                profit >= BE_TRIGGER
                and (
                    current_sl == 0
                    or current_sl > new_sl
                )
            ):

                await connection.modify_position(
                    p["id"],
                    new_sl,
                    tp
                )

                telegram(
                    f"BREAK EVEN SELL {SYMBOL}\n"
                    f"SL -> {new_sl}"
                )


# =========================================================
# PSAR TRAILING
# =========================================================

async def manage_psar_trailing(
    connection,
    account
):

    pos = await positions(connection)

    if not pos:
        return

    df = await get_closed_candles(
        account
    )

    psar, _ = calculate_psar(df)

    point, digits = await symbol_data(
        connection
    )

    price = await connection.get_symbol_price(
        SYMBOL
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for p in pos:

        side = p["type"]

        open_price = float(
            p["openPrice"]
        )

        current_sl = float(
            p.get("stopLoss") or 0
        )

        tp = p.get("takeProfit")

        # BUY
        if side == "POSITION_TYPE_BUY":

            profit = (
                bid - open_price
            ) / point

            if profit < BE_TRIGGER:
                continue

            floor = (
                open_price
                + BE_LOCK * point
            )

            new_sl = round(
                max(psar, floor),
                digits
            )

            if (
                new_sl < bid
                and (
                    current_sl == 0
                    or new_sl > current_sl
                )
            ):

                await connection.modify_position(
                    p["id"],
                    new_sl,
                    tp
                )

        # SELL
        elif side == "POSITION_TYPE_SELL":

            profit = (
                open_price - ask
            ) / point

            if profit < BE_TRIGGER:
                continue

            ceiling = (
                open_price
                - BE_LOCK * point
            )

            new_sl = round(
                min(psar, ceiling),
                digits
            )

            if (
                new_sl > ask
                and (
                    current_sl == 0
                    or new_sl < current_sl
                )
            ):

                await connection.modify_position(
                    p["id"],
                    new_sl,
                    tp
                )


# =========================================================
# OTVORENIE OBCHODU
# =========================================================

async def open_trade(
    connection,
    signal,
    psar
):

    point, digits = await symbol_data(
        connection
    )

    for attempt in range(2):

        price = await connection.get_symbol_price(
            SYMBOL
        )

        ask = float(price["ask"])
        bid = float(price["bid"])

        if signal == "BUY":

            entry = ask

            sl = round(
                entry
                - SL_POINTS * point,
                digits
            )

            tp = round(
                entry
                + TP_POINTS * point,
                digits
            )

                else:
