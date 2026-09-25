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
# XAUUSD | M1 | AGGRESSIVE LIVE PSAR 1st DOT
# TP/SL/BE sa rataju zo SKUTOCNEJ MT5 openPrice
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 8.00       # TP +8.00 od realnej openPrice
SL_DISTANCE = 10.00      # SL -10.00 od realnej openPrice

BE_TRIGGER = 7.00        # pri +7.00
BE_LOCK = 5.00           # zamkne +5.00

COMMENT = "RIObot GOLD M1 PSAR 1DOT EXACT"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m1_cache.json"


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
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

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
            f"TELEGRAM WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )


# =========================================================
# M1 CACHE
# =========================================================

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
        ) as file:

            data = json.load(
                file
            )

        if not isinstance(
            data,
            list
        ):

            return []

        clean = []

        for candle in data[
            -MAX_CACHE_BARS:
        ]:

            if all(
                key in candle
                for key in (
                    "time",
                    "open",
                    "high",
                    "low",
                    "close"
                )
            ):

                clean.append(
                    candle
                )

        return clean

    except Exception as exc:

        print(
            f"CACHE LOAD WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        return []


def save_cache(
    candles
):

    try:

        with open(
            CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                candles[
                    -MAX_CACHE_BARS:
                ],
                file
            )

    except Exception as exc:

        print(
            f"CACHE SAVE WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )


def update_candle_cache(
    candles,
    candle
):

    if candle is None:
        return

    if (
        candles
        and
        candles[-1]["time"]
        == candle["time"]
    ):

        candles[-1] = candle

    else:

        candles.append(
            candle
        )

    del candles[
        :-MAX_CACHE_BARS
    ]

    save_cache(
        candles
    )


# =========================================================
# CURRENT LIVE M1 CANDLE
# =========================================================

async def get_current_m1_candle(
    region
):

    url = (
        f"https://mt-client-api-v1."
        f"{region}."
        f"agiliumtrade.ai/"
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
                    M_TOKEN
            },
            timeout=20
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

        candle = (
            await asyncio.to_thread(
                fetch
            )
        )

        if not candle:
            return None

        required = (
            "time",
            "open",
            "high",
            "low",
            "close"
        )

        if not all(
            key in candle
            for key in required
        ):

            print(
                "M1 CANDLE WARNING: "
                f"missing fields: "
                f"{candle}",
                flush=True
            )

            return None

        return {

            "time":
                str(
                    candle["time"]
                ),

            "open":
                float(
                    candle["open"]
                ),

            "high":
                float(
                    candle["high"]
                ),

            "low":
                float(
                    candle["low"]
                ),

            "close":
                float(
                    candle["close"]
                )
        }

    except Exception as exc:

        print(
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        return None


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(
    df
):

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
        df
    )

    if count < 3:

        return [
            None
        ] * count

    psar = [
        None
    ] * count

    bull = True
    af = PSAR_STEP

    ep = highs[0]
    sar = lows[0]

    psar[0] = sar

    for i in range(
        1,
        count
    ):

        sar = (
            sar
            + af
            * (
                ep
                - sar
            )
        )

        if bull:

            if i >= 2:

                sar = min(
                    sar,
                    lows[i - 1],
                    lows[i - 2]
                )

            else:

                sar = min(
                    sar,
                    lows[i - 1]
                )

            if lows[i] < sar:

                bull = False

                sar = ep
                ep = lows[i]
                af = PSAR_STEP

            elif highs[i] > ep:

                ep = highs[i]

                af = min(
                    af
                    + PSAR_STEP,
                    PSAR_MAX
                )

        else:

            if i >= 2:

                sar = max(
                    sar,
                    highs[i - 1],
                    highs[i - 2]
                )

            else:

                sar = max(
                    sar,
                    highs[i - 1]
                )

            if highs[i] > sar:

                bull = True

                sar = ep
                ep = highs[i]
                af = PSAR_STEP

            elif lows[i] < ep:

                ep = lows[i]

                af = min(
                    af
                    + PSAR_STEP,
                    PSAR_MAX
                )

        psar[i] = sar

    return psar


# =========================================================
# DATAFRAME
# =========================================================

def make_m1_dataframe(
    candles
):

    if (
        len(candles)
        < MIN_PSAR_BARS
    ):

        return None

    df = pd.DataFrame(
        candles
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

    df = (
        df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ]
        )
        .reset_index(
            drop=True
        )
    )

    if (
        len(df)
        < MIN_PSAR_BARS
    ):

        return None

    df["psar"] = psar_values(
        df
    )

    return df


# =========================================================
# AGGRESSIVE LIVE SIGNAL
# PRVA PSAR BODKA = VSTUP
# =========================================================

def get_live_signal(
    df
):

    if (
        df is None
        or len(df) < 3
    ):

        return None

    previous = (
        df.iloc[-2]
    )

    current = (
        df.iloc[-1]
    )

    previous_close = float(
        previous["close"]
    )

    current_close = float(
        current["close"]
    )

    previous_psar = float(
        previous["psar"]
    )

    current_psar = float(
        current["psar"]
    )

    # BUY
    # PSAR bol NAD cenou
    # prva LIVE bodka prejde POD cenu

    if (
        previous_psar
        > previous_close
        and
        current_psar
        < current_close
    ):

        return "BUY"

    # SELL
    # PSAR bol POD cenou
    # prva LIVE bodka prejde NAD cenu

    if (
        previous_psar
        < previous_close
        and
        current_psar
        > current_close
    ):

        return "SELL"

    return None


# =========================================================
# POSITIONS
# =========================================================

async def
