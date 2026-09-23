import asyncio
import json
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD
# XAUUSD | M5 | LIVE PSAR 2nd DOT | MT5 cloud-g2
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_POINTS = 800.0
SL_POINTS = 1000.0

BE_TRIGGER = 500.0
BE_LOCK = 300.0

COMMENT = "RIObot GOLD M5 LIVE PSAR 2DOT"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30

META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m5_cache.json"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# RENDER WEB SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD ACTIVE", 200


def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()


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
                "text": message,
            },
            timeout=10,
        )

    except Exception as exc:
        print(
            f"TELEGRAM ERROR: {exc}",
            flush=True,
        )


# =========================================================
# M5 CACHE
# =========================================================

def load_cache():

    try:

        if not os.path.exists(CACHE_FILE):
            return []

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(data, list):
            return []

        clean = []

        for candle in data[-MAX_CACHE_BARS:]:

            if all(
                key in candle
                for key in (
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                )
            ):
                clean.append(candle)

        return clean

    except Exception as exc:

        print(
            f"CACHE LOAD WARNING: {exc}",
            flush=True,
        )

        return []


def save_cache(candles):

    try:

        with open(
            CACHE_FILE,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                candles[-MAX_CACHE_BARS:],
                f,
            )

    except Exception as exc:

        print(
            f"CACHE SAVE WARNING: {exc}",
            flush=True,
        )


# =========================================================
# CURRENT LIVE M5 CANDLE
# =========================================================

async def get_current_m5_candle():

    url = (
        f"https://mt-client-api-v1.{META_REGION}."
        f"agiliumtrade.ai/users/current/accounts/"
        f"{M_ACC}/symbols/{SYMBOL}/"
        f"current-candles/5m?keepSubscription=true"
    )

    def fetch():

        response = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "auth-token": M_TOKEN,
            },
            timeout=20,
        )

        if response.status_code != 200:

            raise RuntimeError(
                f"M5 candle HTTP "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            )

        return response.json()

    try:

        candle = await asyncio.to_thread(
            fetch
        )

        if not candle:
            return None

        required = (
            "time",
            "open",
            "high",
            "low",
            "close",
        )

        if not all(
            key in candle
            for key in required
        ):

            print(
                f"M5 CANDLE WARNING: "
                f"missing fields: {candle}",
                flush=True,
            )

            return None

        return {
            "time": str(candle["time"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
        }

    except Exception as exc:

        print(
            f"M5 CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        return None


def update_candle_cache(
    candles,
    candle,
):

    if candle is None:
        return candles

    if not candles:

        candles.append(candle)

    elif (
        candles[-1]["time"]
        == candle["time"]
    ):

        candles[-1] = candle

    else:

        candles.append(candle)

    candles[:] = (
        candles[-MAX_CACHE_BARS:]
    )

    save_cache(candles)

    return candles


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(df):

    highs = (
        df["high"]
        .astype(float)
        .tolist()
    )

    lows = (
        df["low"]
        .astype(float)
        .tolist()
    )

    count = len(df)

    if count < 3:
        return [None] * count

    psar = [None] * count

    bull = True
    af =
