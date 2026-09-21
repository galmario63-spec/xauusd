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

# CENTOVÝ ÚČET
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
        raise ValueError("Málo sviečok pre PSAR")

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

        psar[i] = psar[i - 1] + af * (
            ep - psar[i - 1]
        )

        # BULL
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

            else:

                if high[i] > ep:
                    ep = high[i]
                    af = min(
                        af + step,
                        max_af
                    )

        # BEAR
        else:

            if i >= 2:
                psar[i] = max(
                    psar[i],
                    high[i - 1],
