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
# RIObot GOLD - M1 PSAR + M5 CONFIRM + BREAKOUT/RETEST
# =========================================================
SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 8.00
SL_DISTANCE = 10.00

BE1_TRIGGER_DISTANCE = 4.00
BE1_LOCK_DISTANCE = 1.00
BE2_TRIGGER_DISTANCE = 7.00
BE2_LOCK_DISTANCE = 5.00

SIGNAL_CONFIRM_SECONDS = 10
SETUP_EXPIRY_SECONDS = 4 * 60
RETEST_EXPIRY_SECONDS = 2 * 60

IMPULSE_LOOKBACK = 6
IMPULSE_MULTIPLIER = 2.40

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30
NEWS_FETCH_SECONDS = 30 * 60
NEWS_STALE_SECONDS = 3 * 60 * 60

DEFAULT_META_REGION = "london"
MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
HISTORY_SEED_BARS = 80
M1_CACHE_FILE = "m1_cache.json"
M5_CACHE_FILE = "m5_cache.json"
COMMENT = "RIO M1+M5 PSAR RETEST"

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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


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
            data={"chat_id": T_CHAT, "text": message},
            timeout=10,
        )
    except Exception as exc:
        print(
            f"TELEGRAM WARNING: {type(exc).__name__}: {exc}",
            flush=True
        )


# =========================================================
# CACHE
# =========================================================
def load_cache(filename):
    try:
        if not os.path.exists(filename):
            return []

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            return []

        clean = [
            c for c in data[-MAX_CACHE_BARS:]
            if all(
                k in c
                for k in ("time", "open", "high", "low", "close")
            )
        ]

        clean.sort(key=lambda x: str(x["time"]))
        return clean

    except Exception as exc:
        print(
            f"CACHE LOAD WARNING {filename}: {exc}",
            flush=True
        )
        return []


def save_cache(candles, filename):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                candles[-MAX_CACHE_BARS:],
                f
            )
    except Exception as exc:
        print(
            f"CACHE SAVE WARNING {filename}: {exc}",
            flush=True
        )


def update_candle_cache(candles, candle, filename):
    if candle is None:
        return

    for i in range(len(candles) - 1, -1, -1):
        if str(candles[i]["time"]) == str(candle["time"]):
            candles[i] = candle
            break
    else:
        candles.append(candle)

    candles.sort(key=lambda x: str(x["time"]))
    del candles[:-MAX_CACHE_BARS]
    save_cache(candles, filename)


# =========================================================
# METAAPI CANDLES
# =========================================================
def clean_candle(candle):
    if not candle:
        return None

    if not all(
        k in candle
        for k in ("time", "open", "high", "low", "close")
    ):
        return None

    return {
        "time": str(candle["time"]),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
    }


async def get_current_candle(region, timeframe):
    url = (
        f"https://mt-client-api-v1.{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/symbols/{SYMBOL}/"
        f"current-candles/{timeframe}?keepSubscription=true"
    )

    def fetch():
        r = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "auth-token": M_TOKEN
            },
            timeout=20,
        )

        if r.status_code != 200:
            raise RuntimeError(
                f"{timeframe} candle HTTP "
                f"{r.status_code}: {r.text[:250]}"
            )

        return r.json()

    try:
        return clean_candle(
            await asyncio.to_thread(fetch)
        )

    except Exception as exc:
        print(
            f"{timeframe} CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )
        return None


async def get_historical_candles(
    region,
    timeframe,
    limit=HISTORY_SEED_BARS
):
    url = (
        f"https://mt-market-data-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/"
        f"historical-market-data/symbols/{SYMBOL}/"
        f"timeframes/{timeframe}/candles?limit={limit}"
    )

    def fetch():
        r = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "auth-token": M_TOKEN
            },
            timeout=40,
        )

        if r.status_code != 200:
            raise RuntimeError(
                f"historical {timeframe} HTTP "
                f"{r.status_code}: {r.text[:250]}"
            )

        return r.json()

    try:
        raw = await asyncio.to_thread(fetch)

        candles = []

        if isinstance(raw, list):
            for item in raw:
                candle = clean_candle(item)

                if candle:
                    candles.append(candle)

        candles.sort(
            key=lambda x: str(x["time"])
        )

        return candles[-MAX_CACHE_BARS:]

    except Exception as exc:
        print(
            f"HISTORY {timeframe} WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )
        return []


async def seed_history(state):
    if state.get("history_seeded"):
        return

    m1_history, m5_history = await asyncio.gather(
        get_historical_candles(
            state["meta_region"],
            "1m"
        ),
        get_historical_candles(
            state["meta_region"],
            "5m"
        ),
    )

    if m1_history:
        state["m1_candles"] = m1_history
        save_cache(
            m1_history,
            M1_CACHE_FILE
        )

    if m5_history:
        state["m5_candles"] = m5_history
        save_cache(
            m5_history,
            M5_CACHE_FILE
        )

    state["history_seeded"] = (
        len(state["m1_candles"]) >= MIN_PSAR_BARS
        and
        len(state["m5_candles"]) >= MIN_PSAR_BARS
    )

    if state["history_seeded"]:
        print(
            f"HISTORY READY: "
            f"M1={len(state['m1_candles'])} "
            f"M5={len(state['m5_candles'])}",
            flush=True
        )


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


def make_dataframe(candles):
    if len(candles) < MIN_PSAR_BARS:
        return None

    df = pd.DataFrame(candles)

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

    df["psar"] = psar_values(df)

    return df


# =========================================================
# SIGNALS
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

    if (
        previous_psar > previous_close
        and current_psar < current_close
    ):
        return "BUY"

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


def m5_confirms(df_m5, side):
    # Posledné 2 M5 PSAR bodky
    # musia byť v rovnakom smere.

    if (
        df_m5 is None
        or len(df_m5) < 2
    ):
        return False

    last_two = df_m5.iloc[-2:]

    if side == "BUY":
        return all(
            float(row["psar"])
            < float(row["close"])
            for _, row
            in last_two.iterrows()
        )

    if side == "SELL":
        return all(
            float(row["psar"])
            > float(row["close"])
            for _, row
            in last_two.iterrows()
        )

    return False


# =========================================================
# PRICE ACTION FILTERS
# =========================================================
def recent_average_range(df):
    if (
        df is None
        or len(df) < 4
    ):
        return 0.0

    closed = (
        df.iloc[:-1]
        .tail(IMPULSE_LOOKBACK)
    )

    ranges = (
        closed["high"].astype(float)
        - closed["low"].astype(float)
    )

    ranges = ranges[
        ranges > 0
    ]

    if ranges.empty:
        return 0.0

    return float(
        ranges.mean()
    )


def is_impulse_candle(df):
    avg_range = recent_average_range(
        df
    )

    if avg_range <= 0:
        return False

    current = df.iloc[-1]

    current_range = (
        float(current["high"])
        - float(current["low"])
    )

    return (
        current_range
        >= avg_range
        * IMPULSE_MULTIPLIER
    )


def setup_parameters(df):
    avg_range = recent_average_range(
        df
    )

    if avg_range <= 0:
        avg_range = 0.80

    return {
        "max_chase":
            min(
                max(
                    avg_range * 0.90,
                    0.50
                ),
                1.80
            ),

        "retest_tolerance":
            min(
                max(
                    avg_range * 0.30,
                    0.15
                ),
                0.55
            ),

        "invalidation":
            min(
                max(
                    avg_range * 0.55,
                    0.30
                ),
                1.00
            ),

        "reentry_confirm":
            min(
                max(
                    avg_range * 0.20,
                    0.10
                ),
                0.35
            ),

        "min_extension":
            min(
                max(
                    avg_range * 0.35,
                    0.20
                ),
                0.60
            ),
    }


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
    specification = await asyncio.wait_for(
        connection.get_symbol_specification(
            SYMBOL
        ),
        timeout=META_TIMEOUT
    )

    price = await asyncio.wait_for(
        connection.get_symbol_price(
            SYMBOL
        ),
        timeout=META_TIMEOUT
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
        or
        10 ** (-digits)
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
# SL / TP
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
        return (
            round(
                entry - SL_DISTANCE,
                digits
            ),
            round(
                entry + TP_DISTANCE,
                digits
            )
        )

    return (
        round(
            entry + SL_DISTANCE,
            digits
        ),
        round(
            entry - TP_DISTANCE,
            digits
        )
    )


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

    _, digits, _, _ = await get_market(
        connection
    )

    epsilon = (
        10 ** (-digits)
        / 2
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

        exact_sl, exact_tp = exact_levels(
            side,
            entry,
            digits
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

        if (
            current_sl is None
            or abs(
                current_sl
                - target_sl
            )
            > epsilon
            or current_tp is None
            or abs(
                current_tp
                - exact_tp
            )
            > epsilon
        ):
            await asyncio.wait_for(
                connection.modify_position(
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
    _, digits, bid, ask = await get_market(
        connection
    )

    provisional_entry = (
        ask
        if side == "BUY"
        else bid
    )

    sl, tp = exact_levels(
        side,
        provisional_entry,
        digits
    )

    if side == "BUY":

        result = await asyncio.wait_for(
            connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment":
                        COMMENT
                }
            ),
            timeout=META_TIMEOUT
        )

    else:

        result = await asyncio.wait_for(
            connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment":
                        COMMENT
                }
            ),
            timeout=META_TIMEOUT
        )

    position = await wait_for_symbol_position(
        connection,
        side
    )

    if position:

        position_id = (
            position.get(
                "id"
            )
        )

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or 0
        )

        sl, tp = exact_levels(
            side,
            entry,
            digits
        )

        await asyncio.wait_for(
            connection.modify_position(
                position_id,
                sl,
                tp
            ),
            timeout=META_TIMEOUT
        )

        print(
            f"ORDER OK {side} "
            f"ENTRY={entry:.2f} "
            f"SL={sl:.2f} "
            f"TP={tp:.2f}",
            flush=True
        )

        telegram(
            "RIObot GOLD\n\n"
            f"{side} {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            f"Entry: {entry:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP: {tp:.2f}\n"
            "M1 PSAR: 1st DOT + 10s\n"
            "M5: 2 PSAR dots confirm\n"
            "ENTRY: breakout -> retest -> confirmation\n"
            "BE1: +4.00 -> +1.00\n"
            "BE2: +7.00 -> +5.00\n"
            "NEWS: HIGH USD -15m / +30m"
        )

    return result


# =========================================================
# BREAK EVEN
# =========================================================
async def manage_be(
    connection
):
    positions = await get_positions(
        connection
    )

    if not positions:
        return

    _, digits, bid, ask = await get_market(
        connection
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

        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):

            profit = (
                bid - entry
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

        elif side in (
            "SELL",
            "POSITION_TYPE_SELL"
        ):

            profit = (
                entry - ask
            )

            if (
                profit
                >= BE2_TRIGGER_DISTANCE
            ):
                new_sl = round(
                    entry
                    - BE2_LOCK_DISTANCE,
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
                    - BE1_LOCK_DISTANCE,
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
                > new_sl
            )

        else:
            continue

        if improve:

            await asyncio.wait_for(
                connection.modify_position(
                    position_id,
                    new_sl,
                    current_tp
                ),
                timeout=META_TIMEOUT
            )

            clean_side = (
                side.replace(
                    "POSITION_TYPE_",
                    ""
                )
            )

            telegram(
                f"RIObot GOLD {label}\n\n"
                f"{clean_side} {SYMBOL}\n"
                f"{reached} reached\n"
                f"SL locked {locked}\n"
                f"SL: {new_sl:.2f}"
            )


# =========================================================
# NEWS
# =========================================================
def parse_news_datetime(value):
    try:
        return datetime.fromisoformat(
            str(value)
            .strip()
            .replace(
                "Z",
                "+00:00"
            )
        ).timestamp()

    except Exception:
        return None


async def refresh_news_calendar(
    state,
    force=False
):
    now = time.time()

    if (
        not force
        and now
        < state.get(
            "news_next_fetch",
            0
        )
    ):
        return

    state[
        "news_next_fetch"
    ] = (
        now
        + NEWS_FETCH_SECONDS
    )

    def fetch():
        response = requests.get(
            NEWS_URL,
            headers={
                "Accept":
                    "application/json",
                "User-Agent":
                    "RIObot-GOLD/1.0"
            },
            timeout=20,
        )

        response.raise_for_status()

        return response.json()

    try:

        raw = await asyncio.to_thread(
            fetch
        )

        events = []

        if isinstance(
            raw,
            list
        ):

            for item in raw:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                if str(
                    item.get(
                        "country",
                        ""
                    )
                ).upper().strip() != "USD":
                    continue

                if str(
                    item.get(
                        "impact",
                        ""
                    )
                ).upper().strip() != "HIGH":
                    continue

                timestamp = parse_news_datetime(
                    item.get(
                        "date"
                    )
                )

                if timestamp is not None:
                    events.append(
                        {
                            "ts":
                                timestamp,

                            "title":
                                str(
                                    item.get(
                                        "title",
                                        "HIGH USD"
                                    )
                                ).strip()
                                or "HIGH USD"
                        }
                    )

        events.sort(
            key=lambda x:
                x["ts"]
        )

        state[
            "news_events"
        ] = events

        state[
            "news_last_success"
        ] = now

        state[
            "news_error_notified"
        ] = False

        print(
            f"NEWS CALENDAR OK: "
            f"{len(events)} "
            f"HIGH USD events",
            flush=True
        )

    except Exception as exc:

        print(
            f"NEWS WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        if not state.get(
            "news_error_notified"
        ):
            telegram(
                "RIObot GOLD NEWS WARNING\n\n"
                "Calendar unavailable.\n"
                "New entries are blocked."
            )

            state[
                "news_error_notified"
            ] = True


def news_block_status(state):
    now = time.time()

    last_ok = float(
        state.get(
            "news_last_success",
            0
        )
        or 0
    )

    if (
        last_ok <= 0
        or now - last_ok
        > NEWS_STALE_SECONDS
    ):
        return (
            True,
            "calendar unavailable/stale",
            None
        )

    before = (
        NEWS_BEFORE_MINUTES
        * 60
    )

    after = (
        NEWS_AFTER_MINUTES
        * 60
    )

    for event in state.get(
        "news_events",
        []
    ):
        if (
            event["ts"] - before
            <= now
            <= event["ts"] + after
        ):
            return (
                True,
                "HIGH USD",
                event
            )

    return (
        False,
        None,
        None
    )


# =========================================================
# STATE HELPERS
# =========================================================
def clear_pending(state):
    state[
        "pending_signal_key"
    ] = None

    state[
        "pending_signal_side"
    ] = None

    state[
        "pending_signal_started"
    ] = 0.0


def clear_setup(state):
    state[
        "setup_key"
    ] = None

    state[
        "setup_side"
    ] = None

    state[
        "setup_phase"
    ] = None

    state[
        "setup_level"
    ] = None

    state[
        "setup_started"
    ] = 0.0

    state[
        "setup_breakout_at"
    ] = 0.0

    state[
        "setup_last_price"
    ] = None

    state[
        "setup_extension_seen"
    ] = False

    state[
        "setup_params"
    ] = None


def consume_setup(
    state,
    reason
):
    if state.get(
        "setup_key"
    ):
        state[
            "last_signal_key"
        ] = state[
            "setup_key"
        ]

    print(
        f"SETUP CANCELLED: {reason}",
        flush=True
    )

    clear_setup(
        state
    )


# =========================================================
# BOT SESSION
# =========================================================
async def bot_session(state):
    api = MetaApi(
        M_TOKEN
    )

    connection = None

    try:

        account = await asyncio.wait_for(
            api
            .metatrader_account_api
            .get_account(
                M_ACC
            ),
            timeout=META_TIMEOUT
        )

        if (
            getattr(
                account,
                "state",
                None
            )
            != "DEPLOYED"
        ):
            await asyncio.wait_for(
                account.deploy(),
                timeout=180
            )

        if (
            getattr(
                account,
                "connection_status",
                None
            )
            != "CONNECTED"
        ):
            await asyncio.wait_for(
                account.wait_connected(),
                timeout=180
            )

        state[
            "meta_region"
        ] = str(
            getattr(
                account,
                "region",
                None
            )
            or DEFAULT_META_REGION
        ).lower()

        connection = (
            account
            .get_rpc_connection()
        )

        await asyncio.wait_for(
            connection.connect(),
            timeout=60
        )

        await asyncio.wait_for(
            connection
            .wait_synchronized(),
            timeout=120
        )

        print(
            "RIObot GOLD CONNECTED",
            flush=True
        )

        await seed_history(
            state
        )

        await refresh_news_calendar(
            state,
            force=True
        )

        if not state[
            "ever_connected"
        ]:

            telegram(
                "RIObot GOLD START / CONNECTED\n\n"
                f"Symbol: {SYMBOL}\n"
                f"Lot: {LOT_SIZE}\n"
                "M1 PSAR: 1st DOT + 10s signal\n"
                "M5: 2 PSAR dots same direction\n"
                "ENTRY: breakout -> retest -> confirmation\n"
                "NO CHASE / NO BIG IMPULSE\n"
                "SETUP EXPIRES: 4 min\n"
                "NEW PSAR FLIP required after trade\n"
                "MAX: 1 XAUUSD position\n"
                "TP: +8.00\n"
                "SL: -10.00\n"
                "BE1: +4.00 -> +1.00\n"
                "BE2: +7.00 -> +5.00\n"
                "NEWS: HIGH USD -15m / +30m"
            )

            state[
                "ever_connected"
            ] = True

        else:

            telegram(
                "RIObot GOLD RECONNECTED\n\n"
                "MetaApi connection restored."
            )

        loop_errors = 0

        while True:

            try:

                await ensure_exact_stops(
                    connection
                )

                await manage_be(
                    connection
                )

                await refresh_news_calendar(
                    state
                )

                m1_candle, m5_candle = (
                    await asyncio.gather(
                        get_current_candle(
                            state[
                                "meta_region"
                            ],
                            "1m"
                        ),

                        get_current_candle(
                            state[
                                "meta_region"
                            ],
                            "5m"
                        ),
                    )
                )

                if (
                    m1_candle is None
                    or m5_candle is None
                ):
                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                update_candle_cache(
                    state[
                        "m1_candles"
                    ],
                    m1_candle,
                    M1_CACHE_FILE
                )

                update_candle_cache(
                    state[
                        "m5_candles"
                    ],
                    m5_candle,
                    M5_CACHE_FILE
                )

                df_m1 = make_dataframe(
                    state[
                        "m1_candles"
                    ]
                )

                df_m5 = make_dataframe(
                    state[
                        "m5_candles"
                    ]
                )

                if (
                    df_m1 is None
                    or df_m5 is None
                ):

                    print(
                        f"WARMUP "
                        f"M1={len(state['m1_candles'])}/6 "
                        f"M5={len(state['m5_candles'])}/6",
                        flush=True
                    )

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                signal = get_live_signal(
                    df_m1
                )

                m1_side = (
                    get_current_psar_side(
                        df_m1
                    )
                )

                candle_time = str(
                    df_m1.iloc[-1][
                        "time"
                    ]
                )

                positions = await get_positions(
                    connection
                )

                # =========================================
                # LEN 1 OBCHOD
                # =========================================
                if positions:

                    state[
                        "had_position"
                    ] = True

                    clear_pending(
                        state
                    )

                    clear_setup(
                        state
                    )

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =========================================
                # OBCHOD SA ZAVREL
                # ČAKÁME NA NOVÝ PSAR FLIP
                # =========================================
                if state.get(
                    "had_position"
                ):

                    state[
                        "had_position"
                    ] = False

                    state[
                        "need_new_flip_after_close"
                    ] = True

                    state[
                        "close_candle_time"
                    ] = candle_time

                    clear_pending(
                        state
                    )

                    clear_setup(
                        state
                    )

                    print(
                        "POSITION CLOSED -> "
                        "WAITING FOR NEW PSAR FLIP",
                        flush=True
                    )

                # =========================================
                # NEWS
                # =========================================
                (
                    blocked,
                    news_reason,
                    news_event
                ) = news_block_status(
                    state
                )

                if blocked:

                    clear_pending(
                        state
                    )

                    clear_setup(
                        state
                    )

                    event_key = (
                        f"{news_reason}:"
                        f"{news_event['ts']}"
                        if news_event
                        else str(
                            news_reason
                        )
                    )

                    if (
                        state.get(
                            "news_block_notified"
                        )
                        != event_key
                    ):

                        if news_event:

                            event_time = (
                                datetime
                                .fromtimestamp(
                                    news_event[
                                        "ts"
                                    ]
                                )
                                .astimezone()
                                .strftime(
                                    "%H:%M"
                                )
                            )

                            telegram(
                                "RIObot GOLD NEWS BLOCK\n\n"
                                f"HIGH USD: "
                                f"{news_event['title']}\n"
                                f"Time: {event_time}\n"
                                "No new trade -15m / +30m."
                            )

                        else:

                            telegram(
                                "RIObot GOLD NEWS BLOCK\n\n"
                                "Calendar unavailable/stale.\n"
                                "No new trades."
                            )

                        state[
                            "news_block_notified"
                        ] = event_key

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                state[
                    "news_block_notified"
                ] = None

                # =========================================
                # M1 PSAR 1. BODKA = IBA SIGNÁL
                # =========================================
                if signal:

                    signal_key = (
                        f"{candle_time}:"
                        f"{signal}"
                    )

                    # Po zavretí obchodu
                    # nesmie použiť flip
                    # z tej istej M1 sviečky.
                    if (
                        state.get(
                            "need_new_flip_after_close"
                        )
                        and candle_time
                        == state.get(
                            "close_candle_time"
                        )
                    ):
                        signal_key = None

                    if signal_key:

                        if state.get(
                            "need_new_flip_after_close"
                        ):
                            state[
                                "need_new_flip_after_close"
                            ] = False

                        if (
                            signal_key
                            != state.get(
                                "last_signal_key"
                            )
                            and
                            signal_key
                            != state.get(
                                "setup_key"
                            )
                        ):

                            if (
                                state.get(
                                    "pending_signal_key"
                                )
                                != signal_key
                            ):

                                clear_setup(
                                    state
                                )

                                state[
                                    "pending_signal_key"
                                ] = signal_key

                                state[
                                    "pending_signal_side"
                                ] = signal

                                state[
                                    "pending_signal_started"
                                ] = (
                                    time.monotonic()
                                )

                                print(
                                    f"1ST DOT PENDING 10S "
                                    f"{signal}",
                                    flush=True
                                )

                            else:

                                elapsed = (
                                    time.monotonic()
                                    - float(
                                        state.get(
                                            "pending_signal_started",
                                            0
                                        )
                                        or 0
                                    )
                                )

                                if (
                                    elapsed
                                    >= SIGNAL_CONFIRM_SECONDS
                                    and signal
                                    == state.get(
                                        "pending_signal_side"
                                    )
                                ):

                                    # M5 musí potvrdiť
                                    # rovnaký smer
                                    if not m5_confirms(
                                        df_m5,
                                        signal
                                    ):

                                        print(
                                            f"SIGNAL REJECTED "
                                            f"{signal}: "
                                            f"M5 not confirmed",
                                            flush=True
                                        )

                                        state[
                                            "last_signal_key"
                                        ] = signal_key

                                        clear_pending(
                                            state
                                        )

                                    # veľká impulzná
                                    # M1 sviečka
                                    elif is_impulse_candle(
                                        df_m1
                                    ):

                                        print(
                                            f"SIGNAL REJECTED "
                                            f"{signal}: "
                                            f"big M1 impulse candle",
                                            flush=True
                                        )

                                        state[
                                            "last_signal_key"
                                        ] = signal_key

                                        clear_pending(
                                            state
                                        )

                                    else:

                                        last_closed = (
                                            df_m1.iloc[-2]
                                        )

                                        level = float(
                                            last_closed[
                                                "high"
                                            ]
                                            if signal
                                            == "BUY"
                                            else
                                            last_closed[
                                                "low"
                                            ]
                                        )

                                        (
                                            _,
                                            _,
                                            bid,
                                            ask
                                        ) = (
                                            await get_market(
                                                connection
                                            )
                                        )

                                        price = (
                                            bid
                                            if signal
                                            == "BUY"
                                            else ask
                                        )

                                        params = (
                                            setup_parameters(
                                                df_m1
                                            )
                                        )

                                        too_far = (
                                            (
                                                signal
                                                == "BUY"
                                                and price
                                                > level
                                                + params[
                                                    "max_chase"
                                                ]
                                            )
                                            or
                                            (
                                                signal
                                                == "SELL"
                                                and price
                                                < level
                                                - params[
                                                    "max_chase"
                                                ]
                                            )
                                        )

                                        if too_far:

                                            print(
                                                f"SIGNAL REJECTED "
                                                f"{signal}: "
                                                f"price already too far",
                                                flush=True
                                            )

                                            state[
                                                "last_signal_key"
                                            ] = signal_key

                                            clear_pending(
                                                state
                                            )

                                        else:

                                            state[
                                                "setup_key"
                                            ] = signal_key

                                            state[
                                                "setup_side"
                                            ] = signal

                                            state[
                                                "setup_phase"
                                            ] = "WAIT_BREAKOUT"

                                            state[
                                                "setup_level"
                                            ] = level

                                            state[
                                                "setup_started"
                                            ] = (
                                                time.monotonic()
                                            )

                                            state[
                                                "setup_breakout_at"
                                            ] = 0.0

                                            state[
                                                "setup_last_price"
                                            ] = price

                                            state[
                                                "setup_extension_seen"
                                            ] = False

                                            state[
                                                "setup_params"
                                            ] = params

                                            clear_pending(
                                                state
                                            )

                                            print(
                                                f"SETUP ARMED "
                                                f"{signal} "
                                                f"LEVEL="
                                                f"{level:.2f}",
                                                flush=True
                                            )

                elif state.get(
                    "pending_signal_key"
                ):

                    clear_pending(
                        state
                    )

                # =========================================
                # ACTIVE SETUP
                # =========================================
                side = state.get(
                    "setup_side"
                )

                if side:

                    now_mono = (
                        time.monotonic()
                    )

                    params = (
                        state.get(
                            "setup_params"
                        )
                        or setup_parameters(
                            df_m1
                        )
                    )

                    level = float(
                        state[
                            "setup_level"
                        ]
                    )

                    phase = state.get(
                        "setup_phase"
                    )

                    # setup po 4 min vyprší
                    if (
                        now_mono
                        - float(
                            state.get(
                                "setup_started",
                                0
                            )
                            or 0
                        )
                        > SETUP_EXPIRY_SECONDS
                    ):

                        consume_setup(
                            state,
                            "expired"
                        )

                    # M1 musí stále držať smer
                    elif m1_side != side:

                        consume_setup(
                            state,
                            f"M1 PSAR changed "
                            f"to {m1_side}"
                        )

                    # M5 musí stále držať smer
                    elif not m5_confirms(
                        df_m5,
                        side
                    ):

                        consume_setup(
                            state,
                            "M5 confirmation lost"
                        )

                    # veľká impulzná sviečka
                    elif is_impulse_candle(
                        df_m1
                    ):

                        consume_setup(
                            state,
                            "big M1 impulse candle"
                        )

                    else:

                        (
                            _,
                            _,
                            bid,
                            ask
                        ) = await get_market(
                            connection
                        )

                        price = (
                            bid
                            if side == "BUY"
                            else ask
                        )

                        previous_price = (
                            state.get(
                                "setup_last_price"
                            )
                        )

                        if (
                            previous_price
                            is None
                        ):
                            previous_price = (
                                price
                            )

                        # =========================
                        # 1) FRESH BREAKOUT
                        # =========================
                        if (
                            phase
                            == "WAIT_BREAKOUT"
                        ):

                            if side == "BUY":

                                breakout = (
                                    previous_price
                                    <= level
                                    and price
                                    > level
                                )

                                overshoot = (
                                    price
                                    - level
                                )

                            else:

                                breakout = (
                                    previous_price
                                    >= level
                                    and price
                                    < level
                                )

                                overshoot = (
                                    level
                                    - price
                                )

                            if breakout:

                                if (
                                    overshoot
                                    > params[
                                        "max_chase"
                                    ]
                                ):

                                    consume_setup(
                                        state,
                                        "breakout too far "
                                        "- no chase"
                                    )

                                else:

                                    state[
                                        "setup_phase"
                                    ] = "WAIT_RETEST"

                                    state[
                                        "setup_breakout_at"
                                    ] = now_mono

                                    state[
                                        "setup_extension_seen"
                                    ] = (
                                        overshoot
                                        >= params[
                                            "min_extension"
                                        ]
                                    )

                                    print(
                                        f"BREAKOUT "
                                        f"{side} "
                                        f"-> WAIT RETEST",
                                        flush=True
                                    )

                        # =========================
                        # 2) WAIT RETEST
                        # =========================
                        elif (
                            phase
                            == "WAIT_RETEST"
                        ):

                            breakout_age = (
                                now_mono
                                - float(
                                    state.get(
                                        "setup_breakout_at",
                                        0
                                    )
                                    or 0
                                )
                            )

                            if (
                                breakout_age
                                > RETEST_EXPIRY_SECONDS
                            ):

                                consume_setup(
                                    state,
                                    "retest timeout"
                                )

                            elif side == "BUY":

                                if (
                                    price
                                    < level
                                    - params[
                                        "invalidation"
                                    ]
                                ):

                                    consume_setup(
                                        state,
                                        "BUY retest invalidated"
                                    )

                                elif (
                                    price
                                    > level
                                    + params[
                                        "max_chase"
                                    ]
                                    * 1.50
                                ):

                                    consume_setup(
                                        state,
                                        "BUY ran too far "
                                        "without retest"
                                    )

                                else:

                                    if (
                                        price
                                        >= level
                                        + params[
                                            "min_extension"
                                        ]
                                    ):

                                        state[
                                            "setup_extension_seen"
                                        ] = True

                                    if (
                                        state.get(
                                            "setup_extension_seen"
                                        )
                                        and
                                        level
                                        - params[
                                            "invalidation"
                                        ]
                                        <= price
                                        <= level
                                        + params[
                                            "retest_tolerance"
                                        ]
                                    ):

                                        state[
                                            "setup_phase"
                                        ] = "WAIT_RECLAIM"

                                        print(
                                            "RETEST BUY OK "
                                            "-> WAIT CONFIRM",
                                            flush=True
                                        )

                            else:

                                if (
                                    price
                                    > level
                                    + params[
                                        "invalidation"
                                    ]
                                ):

                                    consume_setup(
                                        state,
                                        "SELL retest invalidated"
                                    )

                                elif (
                                    price
                                    < level
                                    - params[
                                        "max_chase"
                                    ]
                                    * 1.50
                                ):

                                    consume_setup(
                                        state,
                                        "SELL ran too far "
                                        "without retest"
                                    )

                                else:

                                    if (
                                        price
                                        <= level
                                        - params[
                                            "min_extension"
                                        ]
                                    ):

                                        state[
                                            "setup_extension_seen"
                                        ] = True

                                    if (
                                        state.get(
                                            "setup_extension_seen"
                                        )
                                        and
                                        level
                                        - params[
                                            "retest_tolerance"
                                        ]
                                        <= price
                                        <= level
                                        + params[
                                            "invalidation"
                                        ]
                                    ):

                                        state[
                                            "setup_phase"
                                        ] = "WAIT_RECLAIM"

                                        print(
                                            "RETEST SELL OK "
                                            "-> WAIT CONFIRM",
                                            flush=True
                                        )

                        # =========================
                        # 3) RETEST HOTOVÝ
                        # ČAKÁME NA ODRAZ
                        # =========================
                        elif (
                            phase
                            == "WAIT_RECLAIM"
                        ):

                            ready = False

                            if side == "BUY":

                                if (
                                    price
                                    < level
                                    - params[
                                        "invalidation"
                                    ]
                                ):

                                    consume_setup(
                                        state,
                                        "BUY reclaim invalidated"
                                    )

                                else:

                                    ready = (
                                        price
                                        >= level
                                        + params[
                                            "reentry_confirm"
                                        ]
                                        and price
                                        > previous_price
                                    )

                            else:

                                if (
                                    price
                                    > level
                                    + params[
                                        "invalidation"
                                    ]
                                ):

                                    consume_setup(
                                        state,
                                        "SELL reclaim invalidated"
                                    )

                                else:

                                    ready = (
                                        price
                                        <= level
                                        - params[
                                            "reentry_confirm"
                                        ]
                                        and price
                                        < previous_price
                                    )

                            if (
                                ready
                                and state.get(
                                    "setup_side"
                                )
                            ):

                                (
                                    blocked_again,
                                    _,
                                    _
                                ) = (
                                    news_block_status(
                                        state
                                    )
                                )

                                positions_again = (
                                    await get_positions(
                                        connection
                                    )
                                )

                                if (
                                    not blocked_again
                                    and
                                    not positions_again
                                    and
                                    m5_confirms(
                                        df_m5,
                                        side
                                    )
                                    and
                                    not is_impulse_candle(
                                        df_m1
                                    )
                                ):

                                    used_key = (
                                        state.get(
                                            "setup_key"
                                        )
                                    )

                                    print(
                                        f"ENTRY CONFIRMED "
                                        f"{side} "
                                        f"LEVEL="
                                        f"{level:.2f} "
                                        f"PRICE="
                                        f"{price:.2f}",
                                        flush=True
                                    )

                                    clear_setup(
                                        state
                                    )

                                    await open_trade(
                                        connection,
                                        side
                                    )

                                    state[
                                        "last_signal_key"
                                    ] = used_key

                                    state[
                                        "had_position"
                                    ] = True

                        if state.get(
                            "setup_side"
                        ):

                            state[
                                "setup_last_price"
                            ] = price

                loop_errors = 0

            except Exception as exc:

                loop_errors += 1

                print(
                    f"LOOP WARNING "
                    f"{loop_errors}/"
                    f"{MAX_LOOP_ERRORS}: "
                    f"{type(exc).__name__}: "
                    f"{exc}",
                    flush=True
                )

                if (
                    loop_errors
                    >= MAX_LOOP_ERRORS
                ):
                    raise

                await asyncio.sleep(
                    RECONNECT_SECONDS
                )

            await asyncio.sleep(
                LOOP_SECONDS
            )

    finally:

        if connection is not None:

            try:
                await asyncio.wait_for(
                    connection.close(),
                    timeout=10
                )

            except Exception:
                pass


# =========================================================
# MAIN
# =========================================================
async def main():
    keep_alive()

    if not M_TOKEN:
        raise RuntimeError(
            "M_TOKEN is missing"
        )

    if not M_ACC:
        raise RuntimeError(
            "M_ACC is missing"
        )

    state = {
        "ever_connected":
            False,

        "history_seeded":
            False,

        "last_signal_key":
            None,

        "m1_candles":
            load_cache(
                M1_CACHE_FILE
            ),

        "m5_candles":
            load_cache(
                M5_CACHE_FILE
            ),

        "meta_region":
            DEFAULT_META_REGION,

        "pending_signal_key":
            None,

        "pending_signal_side":
            None,

        "pending_signal_started":
            0.0,

        "setup_key":
            None,

        "setup_side":
            None,

        "setup_phase":
            None,

        "setup_level":
            None,

        "setup_started":
            0.0,

        "setup_breakout_at":
            0.0,

        "setup_last_price":
            None,

        "setup_extension_seen":
            False,

        "setup_params":
            None,

        "had_position":
            False,

        "need_new_flip_after_close":
            False,

        "close_candle_time":
            None,

        "news_events":
            [],

        "news_last_success":
            0.0,

        "news_next_fetch":
            0.0,

        "news_error_notified":
            False,

        "news_block_notified":
            None,
    }

    while True:

        try:
            await bot_session(
                state
            )

        except Exception as exc:

            print(
                f"BOT SESSION ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}",
                flush=True
            )

            telegram(
                "RIObot GOLD CONNECTION ERROR\n\n"
                f"Reconnect in "
                f"{RECONNECT_SECONDS} seconds."
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


if __name__ == "__main__":
    asyncio.run(
        main()
        )
