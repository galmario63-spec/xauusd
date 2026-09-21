import asyncio
import os
import traceback
from threading import Thread

import pandas as pd
import requests
from flask import Flask
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

# TP / SL - 1:1
TP_POINTS = 1500.0
SL_POINTS = 1500.0

# BREAK EVEN
BE_TRIGGER = 500.0
BE_LOCK = 100.0

COMMENT = "Riobot M1 PSAR ONLY"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
MAX_CONNECTION_ERRORS = 2


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


@app.route("/health")
def health():
    return "OK"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port
    )


def keep_alive():
    thread = Thread(
        target=run_server,
        daemon=True
    )
    thread.start()


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
        print("Telegram chyba:", e)


# =========================================================
# PARABOLIC SAR
# =========================================================

def calculate_psar(df, step=0.02, max_af=0.20):

    if len(df) < 5:
        raise ValueError("Malo sviecok pre PSAR")

    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    psar = [0.0] * len(df)

    bull = close[1] >= close[0]
    af = step

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

        # BULL TREND
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
                af = step

            elif high[i] > ep:

                ep = high[i]
                af = min(
                    af + step,
                    max_af
                )

        # BEAR TREND
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
                af = step

            elif low[i] < ep:

                ep = low[i]
                af = min(
                    af + step,
                    max_af
                )

    return float(psar[-1]), bull


# =========================================================
# UZAVRETE M1 SVIECKY
# =========================================================

async def get_closed_candles(
    account,
    symbol,
    timeframe="1m",
    limit=100
):

    candles = await account.get_historical_candles(
        symbol,
        timeframe,
        None,
        limit
    )

    if not candles:
        raise Exception(
            f"Ziadne data pre {symbol}"
        )

    df = pd.DataFrame(candles)

    if len(df) < 11:
        raise Exception(
            "Malo sviecok"
        )

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    for column in [
        "open",
        "high",
        "low",
        "close"
    ]:
        df[column] = pd.to_numeric(
            df[column],
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

    # Odstranime aktualnu neuzavretu sviecku
    df = df.iloc[:-1].copy()

    if len(df) < 10:
        raise Exception(
            "Malo uzavretych sviecok"
        )

    return df


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(
    connection,
    symbol
):

    spec = await connection.get_symbol_specification(
        symbol
    )

    if not spec:
        raise Exception(
            f"Symbol {symbol} nenajdeny"
        )

    return spec


def get_point(spec):

    return float(
        spec.get("tickSize") or 0.01
    )


# =========================================================
# POZICIE
# =========================================================

async def get_positions(
    connection,
    symbol
):

    positions = await connection.get_positions()

    return [
        position
        for position in positions
        if position.get("symbol") == symbol
    ]


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_break_even(
    connection,
    symbol
):

    positions = await get_positions(
        connection,
        symbol
    )

    if not positions:
        return

    spec = await get_symbol_info(
        connection,
        symbol
    )

    point = get_point(spec)
    digits = int(spec.get("digits", 2))

    price = await connection.get_symbol_price(
        symbol
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in positions:

        position_id = position["id"]
        side = position["type"]

        open_price = float(
            position["openPrice"]
        )

        current_sl = float(
            position.get("stopLoss") or 0
        )

        tp = position.get("takeProfit")

        # BUY
        if side == "POSITION_TYPE_BUY":

            profit_points = (
                bid - open_price
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            new_sl = round(
                open_price
                + BE_LOCK * point,
                digits
            )

            if (
                current_sl != 0
                and current_sl >= new_sl
            ):
                continue

            await connection.modify_position(
                position_id,
                new_sl,
                tp
            )

            telegram(
                f"BREAK EVEN BUY\n"
                f"{symbol}\n"
                f"SL -> {new_sl}\n"
                f"Lock +{BE_LOCK:.0f}"
            )

        # SELL
        elif side == "POSITION_TYPE_SELL":

            profit_points = (
                open_price - ask
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            new_sl = round(
                open_price
                - BE_LOCK * point,
                digits
            )

            if (
                current_sl != 0
                and current_sl <= new_sl
            ):
                continue

            await connection.modify_position(
                position_id,
                new_sl,
                tp
            )

            telegram(
                f"BREAK EVEN SELL\n"
                f"{symbol}\n"
                f"SL -> {new_sl}\n"
                f"Lock +{BE_LOCK:.0f}"
            )


# =========================================================
# PSAR TRAILING
# =========================================================

async def manage_psar_trailing(
    connection,
    account,
    symbol
):

    positions = await get_positions(
        connection,
        symbol
    )

    if not positions:
        return

    spec = await get_symbol_info(
        connection,
        symbol
    )

    point = get_point(spec)
    digits = int(spec.get("digits", 2))

    df = await get_closed_candles(
        account,
        symbol,
        "1m",
        100
    )

    psar, _ = calculate_psar(
        df,
        PSAR_STEP,
        PSAR_MAX
    )

    price = await connection.get_symbol_price(
        symbol
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in positions:

        position_id = position["id"]
        side = position["type"]

        open_price = float(
            position["openPrice"]
        )

        current_sl = float(
            position.get("stopLoss") or 0
        )

        tp = position.get("takeProfit")

        # BUY
        if side == "POSITION_TYPE_BUY":

            profit_points = (
                bid - open_price
            ) / point

            if profit_points < BE_TRIGGER:
               
