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
# RIObot GOLD - SETTINGS
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 8.00
SL_DISTANCE = 10.00

# BREAK EVEN 1
BE1_TRIGGER_DISTANCE = 4.00
BE1_LOCK_DISTANCE = 1.00

# BREAK EVEN 2
BE2_TRIGGER_DISTANCE = 7.00
BE2_LOCK_DISTANCE = 5.00

# PSAR potvrdenie
SIGNAL_CONFIRM_SECONDS = 10

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

# NEWS FILTER
NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30
NEWS_FETCH_SECONDS = 30 * 60
NEWS_STALE_SECONDS = 3 * 60 * 60

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m1_cache.json"

COMMENT = "RIO M1 PSAR BREAKOUT"


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
        port=int(os.environ.get("PORT", 10000))
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
            timeout=10,
        )

    except Exception as exc:

        print(
            f"TELEGRAM WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


# =========================================================
# M1 CACHE
# =========================================================

def load_cache():

    try:

        if not os.path.exists(CACHE_FILE):
            return []

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(data, list):
            return []

        return [
            c
            for c in data[-MAX_CACHE_BARS:]
            if all(
                k in c
                for k in (
                    "time",
                    "open",
                    "high",
                    "low",
                    "close"
                )
            )
        ]

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
                candles[-MAX_CACHE_BARS:],
                f
            )

    except Exception as exc:

        print(
            f"CACHE SAVE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


def update_candle_cache(candles, candle):

    if candle is None:
        return

    if (
        candles
        and candles[-1]["time"] == candle["time"]
    ):

        candles[-1] = candle

    else:

        candles.append(candle)

    del candles[:-MAX_CACHE_BARS]

    save_cache(candles)


# =========================================================
# LIVE M1 CANDLE
# =========================================================

async def get_current_m1_candle(region):

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
                "Accept": "application/json",
                "auth-token": M_TOKEN,
            },
            timeout=20,
        )

        if response.status_code != 200:

            raise RuntimeError(
                f"M1 candle HTTP "
                f"{response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:

        candle = await asyncio.to_thread(
            fetch
        )

        if not candle:
            return None

        if not all(
            k in candle
            for k in (
                "time",
                "open",
                "high",
                "low",
                "close"
            )
        ):

            return None

        return {
            "time":
                str(candle["time"]),
            "open":
                float(candle["open"]),
            "high":
                float(candle["high"]),
            "low":
                float(candle["low"]),
            "close":
                float(candle["close"]),
        }

    except Exception as exc:

        print(
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return None


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
    af = PSAR_STEP
    ep = highs[0]
    sar = lows[0]

    psar[0] = sar

    for i in range(1, count):

        sar = (
            sar
            + af
            * (ep - sar)
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
                    af + PSAR_STEP,
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
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        psar[i] = sar

    return psar


# =========================================================
# DATAFRAME
# =========================================================

def make_m1_dataframe(candles):

    if len(candles) < MIN_PSAR_BARS:
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

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    ).reset_index(
        drop=True
    )

    if len(df) < MIN_PSAR_BARS:
        return None

    df["psar"] = psar_values(
        df
    )

    return df


# =========================================================
# PSAR FLIP
# PRVÁ BODKA = IBA SIGNÁL
# =========================================================

def get_live_signal(df):

    if (
        df is None
        or len(df) < 3
    ):

        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

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

    # BUY flip
    if (
        previous_psar > previous_close
        and current_psar < current_close
    ):

        return "BUY"

    # SELL flip
    if (
        previous_psar < previous_close
        and current_psar > current_close
    ):

        return "SELL"

    return None


def get_current_psar_side(df):

    if (
        df is None
        or len(df) < 1
    ):

        return None

    current = df.iloc[-1]

    close = float(
        current["close"]
    )

    psar = float(
        current["psar"]
    )

    if psar < close:
        return "BUY"

    if psar > close:
        return "SELL"

    return None


# =========================================================
# POSITIONS
# =========================================================

async def get_positions(connection):

    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT
    )

    return [
        position
        for position in positions
        if str(
            position.get(
                "symbol",
                ""
            )
        ).upper()
        == SYMBOL.upper()
    ]


# =========================================================
# MARKET
# =========================================================

async def get_market(connection):

    specification = (
        await asyncio.wait_for(
            connection
            .get_symbol_specification(
                SYMBOL
            ),
            timeout=META_TIMEOUT
        )
    )

    price = (
        await asyncio.wait_for(
            connection
            .get_symbol_price(
                SYMBOL
            ),
            timeout=META_TIMEOUT
        )
    )

    digits = int(
        specification.get(
            "digits",
            2
        )
    )

    point = float(
        specification.get(
            "tickSize"
        )
        or 10 ** (-digits)
    )

    bid = float(
        price["bid"]
    )

    ask = float(
        price["ask"]
    )

    return (
        point,
        digits,
        bid,
        ask
    )


# =========================================================
# EXACT SL / TP
# =========================================================

def exact_levels(
    side,
    entry,
    digits
):

    if side in (
        "BUY",
        "POSITION_TYPE_BUY"
    ):

        sl = round(
            entry - SL_DISTANCE,
            digits
        )

        tp = round(
            entry + TP_DISTANCE,
            digits
        )

    else:

        sl = round(
            entry + SL_DISTANCE,
            digits
        )

        tp = round(
            entry - TP_DISTANCE,
            digits
        )

    return sl, tp


async def wait_for_symbol_position(
    connection,
    side,
    attempts=20
):

    for _ in range(attempts):

        positions = await get_positions(
            connection
        )

        for position in positions:

            position_side = str(
                position.get(
                    "type",
                    ""
                )
            ).upper()

            if (
                side == "BUY"
                and position_side
                in (
                    "BUY",
                    "POSITION_TYPE_BUY"
                )
            ):

                return position

            if (
                side == "SELL"
                and position_side
                in (
                    "SELL",
                    "POSITION_TYPE_SELL"
                )
            ):

                return position

        await asyncio.sleep(
            0.25
        )

    return None


async def ensure_exact_stops(
    connection
):

    positions = await get_positions(
        connection
    )

    if not positions:
        return

    _, digits, _, _ = (
        await get_market(
            connection
        )
    )

    epsilon = (
        10 ** (-digits)
        / 2
    )

    for position in positions:

        position_id = position.get(
            "id"
        )

        side = str(
            position.get(
                "type",
                ""
            )
        ).upper()

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or 0
        )

        if (
            not position_id
            or entry <= 0
        ):

            continue

        exact_sl, exact_tp = (
            exact_levels(
                side,
                entry,
                digits
            )
        )

        current_sl_raw = (
            position.get(
                "stopLoss"
            )
        )

        current_tp_raw = (
            position.get(
                "takeProfit"
            )
        )

        current_sl = (
            float(current_sl_raw)
            if current_sl_raw
            not in (
                None,
                0
            )
            else None
        )

        current_tp = (
            float(current_tp_raw)
            if current_tp_raw
            not in (
                None,
                0
            )
            else None
        )

        # BE sa nikdy nevráti naspäť
        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):

            target_sl = (
                current_sl
                if (
                    current_sl is not None
                    and current_sl >= entry
                )
                else exact_sl
            )

        elif side in (
            "SELL",
            "POSITION_TYPE_SELL"
        ):

            target_sl = (
                current_sl
                if (
                    current_sl is not None
                    and current_sl <= entry
                )
                else exact_sl
            )

        else:

            continue

        needs_sl = (
            current_sl is None
            or abs(
                current_sl
                - target_sl
            )
            > epsilon
        )

        needs_tp = (
            current_tp is None
            or abs(
                current_tp
                - exact_tp
            )
            > epsilon
        )

        if (
            needs_sl
            or needs_tp
        ):

            await asyncio.wait_for(
                connection
                .modify_position(
                    position_id,
                    target_sl,
                    exact_tp
                ),
                timeout=META_TIMEOUT
            )


# =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(
    connection,
    side
):

    _, digits, bid, ask = (
        await get_market(
            connection
        )
    )

    provisional_entry = (
        ask
        if side == "BUY"
        else bid
    )

    provisional_sl, provisional_tp = (
        exact_levels(
            side,
            provisional_entry,
            digits
        )
    )

    if side == "BUY":

        result = await asyncio.wait_for(
            connection
            .create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                provisional_sl,
                provisional_tp,
                {
                    "comment":
                        COMMENT
                }
            ),
            timeout=META_TIMEOUT
        )

    else:

        result = await asyncio.wait_for(
            connection
            .create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                provisional_sl,
                provisional_tp,
                {
                    "comment":
                        COMMENT
                }
            ),
            timeout=META_TIMEOUT
        )

    position = (
        await wait_for_symbol_position(
            connection,
            side
        )
    )

    if position:

        position_id = (
            position.get(
                "id"
            )
        )

        actual_entry = float(
            position.get(
                "openPrice",
                0
            )
            or 0
        )

        exact_sl, exact_tp = (
            exact_levels(
                side,
                actual_entry,
                digits
            )
        )

        await asyncio.wait_for(
            connection
            .modify_position(
                position_id,
                exact_sl,
                exact_tp
            ),
            timeout=META_TIMEOUT
        )

        telegram(
            "RIObot GOLD\n\n"
            f"{side} {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            f"Entry: {actual_entry:.2f}\n"
            f"SL: {exact_sl:.2f}\n"
            f"TP: {exact_tp:.2f}\n"
            "Timeframe: M1\n"
            "PSAR: LIVE 1st DOT + 10s\n"
            "ENTRY: fresh break last M1 HIGH/LOW\n"
            "BE1: +4.00 -> +1.00\n"
            "BE2: +7.00 -> +5.00\n"
            "NEWS: HIGH USD -15m / +30m"
        )

    return result


# =========================================================
# BREAK EVEN 1 + 2
# =========================================================

async def manage_be(
    connection
):

    positions = await get_positions(
        connection
    )

    if not positions:
        return

    _, digits, bid, ask = (
        await get_market(
            connection
        )
    )

    for position in positions:

        position_id = (
            position.get(
                "id"
            )
        )

        side = str(
            position.get(
                "type",
                ""
            )
        ).upper()

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or 0
        )

        if (
            not position_id
            or entry <= 0
        ):

            continue

        current_sl_raw = (
            position.get(
                "stopLoss"
            )
        )

        current_tp_raw = (
            position.get(
                "takeProfit"
            )
        )

        current_sl = (
            float(current_sl_raw)
            if current_sl_raw
            not in (
                None,
                0
            )
            else None
        )

        current_tp = (
            float(current_tp_raw)
            if current_tp_raw
            not in (
                None,
                0
            )
            else None
        )

        # =========================
        # BUY
        # =========================

        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):

            profit = (
                bid
                - entry
            )

            if (
                profit
                >= BE2_TRIGGER_DISTANCE
            ):

                new_sl = round(
                    entry
                    + BE2_LOCK_DISTANCE,
                    digits
                )

                label = "BE2"
                reached = "+7.00"
                locked = "+5.00"

            elif (
                profit
                >= BE1_TRIGGER_DISTANCE
            ):

                new_sl = round(
                    entry
                    + BE1_LOCK_DISTANCE,
                    digits
                )

                label = "BE1"
                reached = "+4.00"
                locked = "+1.00"

            else:

                continue

            improve = (
                current_sl is None
                or current_sl
                < new_sl
            )

        # =========================
        # SELL
        # =========================

        elif side
