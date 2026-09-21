import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================
# NASTAVENIA
# =========================

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


# =========================
# ENV
# =========================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================
# FLASK / RENDER
# =========================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot M1 PSAR running"


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


# =========================
# TELEGRAM
# =========================

def telegram(message):
    if not T_TOKEN or not T_CHAT:
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

    except Exception as exc:
        print(
            "Telegram error:",
            exc,
            flush=True
        )


# =========================
# PARABOLIC SAR
# =========================

def psar(df):
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    if len(df) < 5:
        raise ValueError(
            "Not enough candles for PSAR"
        )

    values = [0.0] * len(df)

    bull = close[1] >= close[0]
    af = PSAR_STEP

    if bull:
        ep = high[0]
        values[0] = low[0]
    else:
        ep = low[0]
        values[0] = high[0]

    for i in range(1, len(df)):

        values[i] = (
            values[i - 1]
            + af * (ep - values[i - 1])
        )

        if bull:

            values[i] = min(
                values[i],
                low[i - 1]
            )

            if i >= 2:
                values[i] = min(
                    values[i],
                    low[i - 2]
                )

            if low[i] < values[i]:

                bull = False
                values[i] = ep
                ep = low[i]
                af = PSAR_STEP

            elif high[i] > ep:

                ep = high[i]
                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        else:

            values[i] = max(
                values[i],
                high[i - 1]
            )

            if i >= 2:
                values[i] = max(
                    values[i],
                    high[i - 2]
                )

            if high[i] > values[i]:

                bull = True
                values[i] = ep
                ep = high[i]
                af = PSAR_STEP

            elif low[i] < ep:

                ep = low[i]
                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

    return values[-1], bull


# =========================
# SVIECKY M1
# =========================

async def get_m1_candles(account):

    data = await account.get_historical_candles(
        SYMBOL,
        "1m",
        None,
        100
    )

    if not data or len(data) < 12:
        raise RuntimeError(
            "Not enough M1 candles"
        )

    df = pd.DataFrame(data)

    df = df.sort_values(
        "time"
    ).reset_index(
        drop=True
    )

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

    # Odstranime aktualne otvorenu sviecku.
    return df.iloc[:-1].copy()


# =========================
# POZICIE
# =========================

async def get_positions(connection):

    positions = (
        await connection.get_positions()
    )

    return [
        position
        for position in positions
        if position.get("symbol") == SYMBOL
    ]


# =========================
# SYMBOL INFO
# =========================

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
        specification.get(
            "digits",
            2
        )
    )

    point = float(
        specification.get("tickSize")
        or (10 ** -digits)
    )

    return point, digits


# =========================
# BREAK EVEN
# =========================

async def protect_position(connection):

    positions = await get_positions(
        connection
    )

    if not positions:
        return

    point, digits = (
        await get_point_digits(
            connection
        )
    )

    price = (
        await connection.get_symbol_price(
            SYMBOL
        )
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in positions:

        open_price = float(
            position["openPrice"]
        )

        old_sl = float(
            position.get("stopLoss")
            or 0
        )

        take_profit = (
            position.get("takeProfit")
        )

        position_type = (
            position.get("type")
        )

        try:

            if (
                position_type
                == "POSITION_TYPE_BUY"
            ):

                profit_points = (
                    bid - open_price
                ) / point

                if (
                    profit_points
                    >= BE_TRIGGER
                ):

                    new_sl = round(
                        open_price
                        + BE_LOCK * point,
                        digits
                    )

                    if (
                        old_sl == 0
                        or new_sl > old_sl
                    ):

                        await connection.modify_position(
                            position["id"],
                            new_sl,
                            take_profit
                        )

                        print(
                            "BUY BE:",
                            new_sl,
                            flush=True
                        )

            elif (
                position_type
                == "POSITION_TYPE_SELL"
            ):

                profit_points = (
                    open_price - ask
                ) / point

                if (
                    profit_points
                    >= BE_TRIGGER
                ):

                    new_sl = round(
                        open_price
                        - BE_LOCK * point,
                        digits
                    )

                    if (
                        old_sl == 0
                        or new_sl < old_sl
                    ):

                        await connection.modify_position(
                            position["id"],
                            new_sl,
                            take_profit
                        )

                        print(
                            "SELL BE:",
                            new_sl,
                            flush=True
                        )

        except Exception as exc:

            print(
                "BE modify error:",
                exc,
                flush=True
            )


# =========================
# OTVORENIE OBCHODU
# =========================

async def open_order(
    connection,
    side,
    psar_value
):

    point, digits = (
        await get_point_digits(
            connection
        )
    )

    price = (
        await connection.get_symbol_price(
            SYMBOL
        )
    )

    ask = float(price["ask"])
    bid = float(price["bid"])

    if side == "BUY":
        entry = ask

        sl = round(
            entry
            - SL_POINTS * point,
            digits
        )

        tp = round(
            entry
            + TP_POINTS
