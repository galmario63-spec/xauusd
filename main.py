import asyncio
import json
import os
import time
from datetime import datetime
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD - M1 signal + M5 confirm + breakout/retest
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 8.00
SL_DISTANCE = 10.00

BE1_TRIGGER = 4.00
BE1_LOCK = 1.00

BE2_TRIGGER = 7.00
BE2_LOCK = 5.00

SIGNAL_CONFIRM_SECONDS = 10

SETUP_EXPIRY_SECONDS = 4 * 60
RETEST_EXPIRY_SECONDS = 2 * 60

IMPULSE_LOOKBACK = 6
IMPULSE_MULTIPLIER = 2.40

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

NEWS_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30
NEWS_FETCH_SECONDS = 30 * 60
NEWS_STALE_SECONDS = 3 * 60 * 60

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 300
HISTORY_SEED_BARS = 180

CACHE_FILE = "m1_cache.json"

COMMENT = "RIO M1 M5 RETEST"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# KEEP ALIVE
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD ACTIVE", 200


def run_server():

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
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
        return

    try:

        requests.post(
            f"https://api.telegram.org/"
            f"bot{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10,
        )

    except Exception as exc:

        print(
            f"TELEGRAM WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


# =========================================================
# CACHE / CANDLES
# =========================================================

def clean_candle(c):

    if (
        not c
        or not all(
            k in c
            for k in (
                "time",
                "open",
                "high",
                "low",
                "close"
            )
        )
    ):

        return None

    return {
        "time":
            str(
                c["time"]
            ),

        "open":
            float(
                c["open"]
            ),

        "high":
            float(
                c["high"]
            ),

        "low":
            float(
                c["low"]
            ),

        "close":
            float(
                c["close"]
            ),
    }


def load_cache():

    try:

        if not os.path.exists(
            CACHE_FILE
        ):

            return []

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(
                f
            )

        if not isinstance(
            data,
            list
        ):

            return []

        out = [
            clean_candle(c)
            for c
            in data[
                -MAX_CACHE_BARS:
            ]
        ]

        out = [
            c
            for c in out
            if c
        ]

        out.sort(
            key=lambda x:
                x["time"]
        )

        return out

    except Exception as exc:

        print(
            f"CACHE LOAD WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return []


def save_cache(candles):

    try:

        with open(
            CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                candles[
                    -MAX_CACHE_BARS:
                ],
                f
            )

    except Exception as exc:

        print(
            f"CACHE SAVE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


def update_cache(
    candles,
    candle
):

    if candle is None:
        return

    for i in range(
        len(candles) - 1,
        -1,
        -1
    ):

        if (
            candles[i]["time"]
            ==
            candle["time"]
        ):

            candles[i] = candle
            break

    else:

        candles.append(
            candle
        )

    candles.sort(
        key=lambda x:
            x["time"]
    )

    del candles[
        :-MAX_CACHE_BARS
    ]

    save_cache(
        candles
    )


# =========================================================
# LIVE M1
# =========================================================

async def get_current_m1(
    region
):

    url = (
        f"https://mt-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/"
        f"{M_ACC}/symbols/"
        f"{SYMBOL}/"
        f"current-candles/1m"
        f"?keepSubscription=true"
    )

    def fetch():

        response = requests.get(
            url,
            headers={
                "Accept":
                    "application/json",

                "auth-token":
                    M_TOKEN,
            },
            timeout=20,
        )

        if (
            response.status_code
            != 200
        ):

            raise RuntimeError(
                f"M1 candle HTTP "
                f"{response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:

        raw = (
            await asyncio.to_thread(
                fetch
            )
        )

        return clean_candle(
            raw
        )

    except Exception as exc:

        print(
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return None


# =========================================================
# HISTORICAL M1
# =========================================================

async def get_history_m1(
    region
):

    url = (
        f"https://mt-market-data-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/"
        f"{M_ACC}/historical-market-data/"
        f"symbols/{SYMBOL}/"
        f"timeframes/1m/"
        f"candles?"
        f"limit={HISTORY_SEED_BARS}"
    )

    def fetch():

        response = requests.get(
            url,
            headers={
                "Accept":
                    "application/json",

                "auth-token":
                    M_TOKEN,
            },
            timeout=40,
        )

        if (
            response.status_code
            != 200
        ):

            raise RuntimeError(
                f"historical M1 HTTP "
                f"{response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:

        raw = (
            await asyncio.to_thread(
                fetch
            )
        )

        out = []

        if isinstance(
            raw,
            list
        ):

            for item in raw:

                candle = (
                    clean_candle(
                        item
                    )
                )

                if candle:

                    out.append(
                        candle
                    )

        out.sort(
            key=lambda x:
                x["time"]
        )

        return out[
            -MAX_CACHE_BARS:
        ]

    except Exception as exc:

        print(
            f"HISTORY M1 WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return []


# =========================================================
# PSAR
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

    count = len(
       
