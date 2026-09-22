import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD - NASTAVENIA
# =========================================================

SYMBOL = "XAUUSD"

LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

# XAUUSD pri tickSize 0.01
TP_POINTS = 800.0       # +8.00 pohyb ceny
SL_POINTS = 1000.0      # -10.00 pohyb ceny

# BREAK EVEN
BE_TRIGGER = 500.0      # pri +5.00
BE_LOCK = 300.0         # zamkne +3.00

COMMENT = "RIObot GOLD M5 PSAR 2DOT BE"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30


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
    return "RIObot GOLD ACTIVE"


def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    thread = Thread(target=run_server, daemon=True)
    thread.start()


# =========================================================
# TELEGRAM
# =========================================================

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

    except Exception as e:
        print(
            f"TELEGRAM ERROR: {e}",
            flush=True
        )


# =========================================================
# METAAPI CONNECTION ERROR CHECK
# =========================================================

def is_connection_error(error):

    if isinstance(
        error,
        (
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError
        )
    ):
        return True

    text = str(error).lower()

    connection_words = [
        "websocket",
        "timed out",
        "timeout",
        "failed to connect",
        "disconnected",
        "not connected",
        "connection closed",
        "socket client",
        "connection error"
    ]

    return any(
        word in text
        for word in connection_words
    )


# =========================================================
# SAFE CONNECTION CLOSE
# =========================================================

async def safe_close(connection):

    if connection is None:
        return

    try:

        print(
            "CLOSING OLD METAAPI CONNECTION...",
            flush=True
        )

        await asyncio.wait_for(
            connection.close(),
            timeout=10
        )

    except Exception as e:

        print(
            f"METAAPI CLOSE WARNING: {e}",
            flush=True
        )


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(df):

    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()

    length = len(df)

    if length < 3:
        return [None] * length

    psar = [None] * length

    bull = True
    af = PSAR_STEP
    ep = high[0]
    sar = low[0]

    psar[0] = sar

    for i in range(1, length):

        previous_sar = sar
        sar = previous_sar + af * (ep - previous_sar)

        if bull:

            if i >= 2:
                sar = min(
                    sar,
                    low[i - 1],
                    low[i - 2]
                )
            else:
                sar = min(
                    sar,
                    low[i - 1]
                )

            if low[i] < sar:

                bull = False
                sar = ep
                ep = low[i]
                af = PSAR_STEP

            else:

                if high[i] > ep:
                    ep = high[i]

                    af = min(
                        af + PSAR_STEP,
                        PSAR_MAX
                    )

        else:

            if i
