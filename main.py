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

SYMBOL_REQUEST = "BTCUSD"

# CENTOVÝ ÚČET
LOT_SIZE = 0.30

PSAR_STEP = 0.02
PSAR_MAX = 0.20

EMA_PERIOD = 50

# 1:1
TP_POINTS = 1500.0
SL_POINTS = 1500.0

# BREAK EVEN
BE_TRIGGER = 500.0
BE_LOCK = 100.0

COMMENT = "Riobot 5M+1M BE+PSAR"

LOOP_SECONDS = 10

# METAAPI RECONNECT
RECONNECT_SECONDS = 15


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot is running"


@app.route("/health")
def health():
    return "OK"


def run_flask():
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):

    if not T_TOKEN or not T_CHAT:
        print(message)
        return

    try:
        url = (
            f"https://api.telegram.org/"
            f"bot{T_TOKEN}/sendMessage"
        )

        requests.post(
            url,
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )

    except Exception as e:
        print("Telegram chyba:", e)


# =========================================================
# PSAR
# =========================================================

def calculate_psar(df, step=0.02, max_af=0.20):

    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()

    n = len(df)

    if n < 5:
        return None, None

    psar = [0.0] * n

    bull = True
    af = step
    ep = high[0]

    psar[0] = low[0]

    for i in range(1, n):

        previous_psar = psar[i - 1]

        if bull:

            psar[i] = (
                previous_psar
                + af * (ep - previous_psar)
            )

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

        else:

            psar[i] = (
                previous_psar
                + af * (ep - previous_psar)
            )

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

    return psar[-1], bull


# =========================================================
# EMA
# =========================================================

def calculate_ema(df, period=50):

    return (
        df["close"]
        .ewm(
            span=period,
            adjust=False
        )
        .mean()
    )


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(connection, symbol):

    try:
        return await connection.get_symbol_specification(
            symbol
        )

    except Exception as e:
        print("Symbol info chyba:", e)
        return None


# =========================================================
# POZÍCIE
# =========================================================

async def get_positions(connection, symbol):

    try:
        positions = await connection.get_positions()

        return [
            p for p in positions
            if p.get("symbol") == symbol
        ]

    except Exception as e:
        print("Pozície chyba:", e)
        return []


# =========================================================
# SVIEČKY
# =========================================================

async def get_closed_candles(
    account,
    symbol,
    timeframe,
    limit=100,
    minimum=10
):

    try:

        candles = await account.get_historical_candles(
            symbol=symbol,
