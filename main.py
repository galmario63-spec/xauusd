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
LOT_SIZE = 2.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 10.00
SL_DISTANCE = 10.00

BE1_TRIGGER = 4.00
BE1_LOCK = 1.00

BE2_TRIGGER = 7.00
BE2_LOCK = 4.00

BE3_TRIGGER = 9.00
BE3_LOCK = 7.00

EMA_PERIOD = 50
ADX_PERIOD = 14
ADX_MIN = 20.0

SIGNAL_CONFIRM_SECONDS = 10

SETUP_EXPIRY_SECONDS = 4 * 60
RETEST_EXPIRY_SECONDS = 2 * 60

IMPULSE_LOOKBACK = 6
IMPULSE_MULTIPLIER = 2.40

LOOP_SECONDS = 10
RECONNECT_SECONDS = 3
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30
NEWS_FETCH_SECONDS = 30 * 60
NEWS_STALE_SECONDS = 3 * 60 * 60

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 300
HISTORY_SEED_BARS = 180

CACHE_FILE = "m1_cache.json"

COMMENT = "RIO M1 M5 RETEST EMA50 ADX"


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
        port=int(os.environ.get("PORT", 10000)),
    )


def keep_alive():
    Thread(target=run_server, daemon=True).start()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):
    if not T_TOKEN or not T_CHAT:
        print(
            f"TELEGRAM ENV ERROR: "
            f"T_TOKEN={'OK' if T_TOKEN else 'MISSING'} | "
            f"T_CHAT={'OK' if T_CHAT else 'MISSING'}",
            flush=True,
        )
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message,
            },
            timeout=15,
        )

        print(
            f"TELEGRAM RESPONSE: {response.status_code} "
            f"{response.text[:300]}",
            flush=True,
        )

        if response.status_code == 200:
            print("TELEGRAM OK", flush=True)
            return True

        print("TELEGRAM SEND FAILED", flush=True)
        return False

    except Exception as exc:
        print(
            f"TELEGRAM ERROR: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return False


# =========================================================
# CACHE
# =========================================================

def clean_candle(c):
    if (
        not c
        or not all(
            k in c
            for k in ("time", "open", "high", "low", "close")
        )
    ):
        return None

    return {
        "time": str(c["time"]),
        "open": float(c["open"]),
        "high": float(c["high"]),
        "low": float(c["low"]),
        "close": float(c["close"]),
    }


def load_cache():
    try:
        if not os.path.exists(CACHE_FILE):
            return []

        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            return []

        out = [
            clean_candle(c)
            for c in data[-MAX_CACHE_BARS:]
        ]

        out = [c for c in out if c]
        out.sort(key=lambda x: x["time"])

        return out

    except Exception as exc:
        print(
            f"CACHE LOAD WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return []


def save_cache(candles):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                candles[-MAX_CACHE_BARS:],
                f,
            )

    except Exception as exc:
        print(
            f"CACHE SAVE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


def update_cache(candles, candle):
    if candle is None:
        return

    for i in range(len(candles) - 1, -1, -1):
        if candles[i]["time"] == candle["time"]:
            candles[i] = candle
            break
    else:
        candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    if len(candles) > MAX_CACHE_BARS:
        del candles[:-MAX_CACHE_BARS]

    save_cache(candles)


# =========================================================
# M1 DATA
# =========================================================

async def get_current_m1(region):
    url = (
        f"https://mt-client-api-v1.{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/symbols/{SYMBOL}/"
        f"current-candles/1m?keepSubscription=true"
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
                f"M1 candle HTTP {response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:
        raw = await asyncio.to_thread(fetch)
        return clean_candle(raw)

    except Exception as exc:
        print(
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


async def get_history_m1(region):
    url = (
        f"https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/historical-market-data/"
        f"symbols/{SYMBOL}/timeframes/1m/candles"
        f"?limit={HISTORY_SEED_BARS}"
    )

    def fetch():
        response = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "auth-token": M_TOKEN,
            },
            timeout=40,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"historical M1 HTTP {response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:
        raw = await asyncio.to_thread(fetch)

        out = []

        if isinstance(raw, list):
            for item in raw:
                candle = clean_candle(item)

                if candle:
                    out.append(candle)

        out.sort(key=lambda x: x["time"])

        return out[-MAX_CACHE_BARS:]

    except Exception as exc:
        print(
            f"HISTORY M1 WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return []


# =========================================================
# PSAR
# =========================================================

def psar_values(df):
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()

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
        sar = sar + af * (ep - sar)

        if bull:
            if i >= 2:
                sar = min(
                    sar,
                    lows[i - 1],
                    lows[i - 2],
                )
            else:
                sar = min(
                    sar,
                    lows[i - 1],
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
                    PSAR_MAX,
                )

        else:
            if i >= 2:
                sar = max(
                    sar,
                    highs[i - 1],
                    highs[i - 2],
                )
            else:
                sar = max(
                    sar,
                    highs[i - 1],
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
                    PSAR_MAX,
                )

        psar[i] = sar

    return psar


# =========================================================
# EMA + ADX
# =========================================================

def add_trend_filters(df):
    df = df.copy()

    df["ema50"] = (
        df["close"]
        .astype(float)
        .ewm(
            span=EMA_PERIOD,
            adjust=False,
        )
        .mean()
    )

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        0.0,
        index=df.index,
    )

    minus_dm = pd.Series(
        0.0,
        index=df.index,
    )

    plus_dm[
        (up_move > down_move)
        & (up_move > 0)
    ] = up_move

    minus_dm[
        (down_move > up_move)
        & (down_move > 0)
    ] = down_move

    alpha = 1.0 / ADX_PERIOD

    atr = tr.ewm(
        alpha=alpha,
        adjust=False,
    ).mean()

    plus_smoothed = plus_dm.ewm(
        alpha=alpha,
        adjust=False,
    ).mean()

    minus_smoothed = minus_dm.ewm(
        alpha=alpha,
        adjust=False,
    ).mean()

    safe_atr = atr.replace(
        0,
        float("nan"),
    )

    plus_di = (
        100.0
        * plus_smoothed
        / safe_atr
    )

    minus_di = (
        100.0
        * minus_smoothed
        / safe_atr
    )

    denominator = (
        plus_di + minus_di
    ).replace(
        0,
        float("nan"),
    )

    dx = (
        100.0
        * (plus_di - minus_di).abs()
        / denominator
    )

    df["plus_di"] = plus_di.fillna(0.0)
    df["minus_di"] = minus_di.fillna(0.0)

    df["adx"] = (
        dx.ewm(
            alpha=alpha,
            adjust=False,
        )
        .mean()
        .fillna(0.0)
    )

    return df
def trend_filter_ok(df5, side):
    if df5 is None or df5.empty:
        return False

    row = df5.iloc[-1]

    close = float(row["close"])
    ema50 = float(row["ema50"])
    adx = float(row["adx"])

    plus_di = float(row["plus_di"])
    minus_di = float(row["minus_di"])

    if adx < ADX_MIN:
        return False

    if side == "BUY":
        return (
            close > ema50
            and plus_di > minus_di
        )

    if side == "SELL":
        return (
            close < ema50
            and minus_di > plus_di
        )

    return False


# =========================================================
# DATAFRAMES
# =========================================================

def make_m1_df(candles):
    if len(candles) < MIN_PSAR_BARS:
        return None

    df = pd.DataFrame(candles)

    for col in (
        "open",
        "high",
        "low",
        "close",
    ):
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    ).reset_index(drop=True)

    if len(df) < MIN_PSAR_BARS:
        return None

    df["psar"] = psar_values(df)

    return df


def make_m5_df(m1_candles):
    if len(m1_candles) < 30:
        return None

    df = pd.DataFrame(m1_candles)

    for col in (
        "open",
        "high",
        "low",
        "close",
    ):
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df["dt"] = pd.to_datetime(
        df["time"],
        utc=True,
        errors="coerce",
    )

    df = df.dropna(
        subset=[
            "dt",
            "open",
            "high",
            "low",
            "close",
        ]
    ).sort_values("dt")

    if df.empty:
        return None

    df["bucket"] = (
        df["dt"]
        .dt.floor("5min")
    )

    m5 = (
        df.groupby(
            "bucket",
            as_index=False,
        )
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
        )
    )

    if len(m5) < MIN_PSAR_BARS:
        return None

    m5["time"] = (
        m5["bucket"]
        .dt.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
    )

    m5 = m5[
        [
            "time",
            "open",
            "high",
            "low",
            "close",
        ]
    ].reset_index(drop=True)

    m5["psar"] = psar_values(m5)

    m5 = add_trend_filters(m5)

    return m5


# =========================================================
# HISTORY
# =========================================================

async def seed_history(state):
    if state["history_seeded"]:
        return

    history = await get_history_m1(
        state["region"]
    )

    if history:
        state["m1_candles"] = history
        save_cache(history)

    df1 = make_m1_df(
        state["m1_candles"]
    )

    df5 = make_m5_df(
        state["m1_candles"]
    )

    state["history_seeded"] = (
        df1 is not None
        and df5 is not None
    )

    if state["history_seeded"]:
        print(
            f"HISTORY READY: "
            f"M1={len(df1)} "
            f"M5={len(df5)} "
            f"(M5 derived from M1)",
            flush=True,
        )


# =========================================================
# SIGNALS
# =========================================================

def get_live_flip(df):
    if df is None or len(df) < 3:
        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

    if (
        float(previous["psar"])
        > float(previous["close"])
        and float(current["psar"])
        < float(current["close"])
    ):
        return "BUY"

    if (
        float(previous["psar"])
        < float(previous["close"])
        and float(current["psar"])
        > float(current["close"])
    ):
        return "SELL"

    return None


def current_psar_side(df):
    if df is None or df.empty:
        return None

    row = df.iloc[-1]

    if float(row["psar"]) < float(row["close"]):
        return "BUY"

    if float(row["psar"]) > float(row["close"]):
        return "SELL"

    return None


def m5_confirms(df5, side):
    if df5 is None or len(df5) < 2:
        return False

    rows = df5.iloc[-2:]

    if side == "BUY":
        return all(
            float(row["psar"])
            < float(row["close"])
            for _, row in rows.iterrows()
        )

    if side == "SELL":
        return all(
            float(row["psar"])
            > float(row["close"])
            for _, row in rows.iterrows()
        )

    return False


# =========================================================
# NOVY M1 ENTRY PRESSURE FILTER
# =========================================================

def m1_entry_pressure_ok(df1, side):
    """
    Kontrola 2 poslednych UZAVRETYCH M1 sviecok.
    Aktualna otvorena M1 sviecka sa nepouziva.

    2 medvedie M1 -> blokuje BUY.
    2 bycie M1 -> blokuje SELL.
    """

    if df1 is None or len(df1) < 3:
        return False

    closed = df1.iloc[-3:-1]

    if len(closed) < 2:
        return False

    if side == "BUY":
        bearish_pressure = all(
            float(row["close"]) < float(row["open"])
            for _, row in closed.iterrows()
        )

        return not bearish_pressure

    if side == "SELL":
        bullish_pressure = all(
            float(row["close"]) > float(row["open"])
            for _, row in closed.iterrows()
        )

        return not bullish_pressure

    return False


# =========================================================
# PRICE ACTION
# =========================================================

def average_range(df1):
    if df1 is None or len(df1) < 4:
        return 0.0

    rows = (
        df1.iloc[:-1]
        .tail(IMPULSE_LOOKBACK)
    )

    values = (
        rows["high"].astype(float)
        - rows["low"].astype(float)
    )

    values = values[values > 0]

    if values.empty:
        return 0.0

    return float(values.mean())


def big_impulse(df1):
    avg = average_range(df1)

    if avg <= 0:
        return False

    current = df1.iloc[-1]

    current_range = (
        float(current["high"])
        - float(current["low"])
    )

    return (
        current_range
        >= avg * IMPULSE_MULTIPLIER
    )


def params_for_setup(df1):
    avg = average_range(df1) or 0.80

    return {
        "max_chase": min(
            max(avg * 0.90, 0.50),
            1.80,
        ),
        "retest": min(
            max(avg * 0.30, 0.15),
            0.55,
        ),
        "invalidate": min(
            max(avg * 0.55, 0.30),
            1.00,
        ),
        "confirm": min(
            max(avg * 0.20, 0.10),
            0.35,
        ),
        "extension": min(
            max(avg * 0.35, 0.20),
            0.60,
        ),
    }


# =========================================================
# POSITIONS / MARKET
# =========================================================

async def get_positions(connection):
    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT,
    )

    return [
        p
        for p in positions
        if str(
            p.get("symbol", "")
        ).upper()
        == SYMBOL.upper()
    ]


async def get_market(connection):
    specification = await asyncio.wait_for(
        connection.get_symbol_specification(
            SYMBOL
        ),
        timeout=META_TIMEOUT,
    )

    price = await asyncio.wait_for(
        connection.get_symbol_price(
            SYMBOL
        ),
        timeout=META_TIMEOUT,
    )

    digits = int(
        specification.get(
            "digits",
            2,
        )
    )

    tick = float(
        specification.get("tickSize")
        or 10 ** (-digits)
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    return tick, digits, bid, ask
    # =========================================================
# NORMALIZE PRICE
# =========================================================

def normalize_price(value, tick, digits):
    if tick <= 0:
        return round(
            value,
            digits,
        )

    steps = round(
        value / tick
    )

    return round(
        steps * tick,
        digits,
    )


# =========================================================
# POSITION HELPERS
# =========================================================

def position_side(position):
    raw = str(
        position.get("type", "")
    ).lower()

    if "buy" in raw:
        return "BUY"

    if "sell" in raw:
        return "SELL"

    return None


def position_open_price(position):
    for key in (
        "openPrice",
        "priceOpen",
        "open_price",
    ):
        value = position.get(key)

        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    return None


def position_sl(position):
    for key in (
        "stopLoss",
        "sl",
    ):
        value = position.get(key)

        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    return None


def position_tp(position):
    for key in (
        "takeProfit",
        "tp",
    ):
        value = position.get(key)

        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    return None


def position_id(position):
    for key in (
        "id",
        "positionId",
    ):
        value = position.get(key)

        if value is not None:
            return str(value)

    return None


# =========================================================
# DESIRED SL / TP
# =========================================================

def initial_sl_tp(
    side,
    open_price,
    tick,
    digits,
):
    if side == "BUY":
        sl = open_price - SL_DISTANCE
        tp = open_price + TP_DISTANCE

    else:
        sl = open_price + SL_DISTANCE
        tp = open_price - TP_DISTANCE

    return (
        normalize_price(
            sl,
            tick,
            digits,
        ),
        normalize_price(
            tp,
            tick,
            digits,
        ),
    )


def desired_be_sl(
    side,
    open_price,
    current_price,
    tick,
    digits,
):
    if side == "BUY":
        profit_distance = (
            current_price - open_price
        )

    else:
        profit_distance = (
            open_price - current_price
        )

    lock = None
    stage = None

    if profit_distance >= BE3_TRIGGER:
        lock = BE3_LOCK
        stage = "BE3"

    elif profit_distance >= BE2_TRIGGER:
        lock = BE2_LOCK
        stage = "BE2"

    elif profit_distance >= BE1_TRIGGER:
        lock = BE1_LOCK
        stage = "BE1"

    if lock is None:
        return None, None

    if side == "BUY":
        sl = open_price + lock

    else:
        sl = open_price - lock

    sl = normalize_price(
        sl,
        tick,
        digits,
    )

    return sl, stage


# =========================================================
# POSITION MODIFY
# =========================================================

async def modify_position(
    connection,
    pos_id,
    stop_loss,
    take_profit,
):
    if not pos_id:
        return False

    try:
        await asyncio.wait_for(
            connection.modify_position(
                pos_id,
                stop_loss,
                take_profit,
            ),
            timeout=META_TIMEOUT,
        )

        return True

    except Exception as exc:
        print(
            f"MODIFY POSITION ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        return False


# =========================================================
# RESTORE / PROTECT OPEN POSITION
# =========================================================

async def protect_position(
    connection,
    position,
):
    side = position_side(position)

    if side not in (
        "BUY",
        "SELL",
    ):
        return

    open_price = position_open_price(
        position
    )

    pos_id = position_id(
        position
    )

    if open_price is None or not pos_id:
        return

    tick, digits, bid, ask = (
        await get_market(connection)
    )

    current_price = (
        bid
        if side == "BUY"
        else ask
    )

    current_sl = position_sl(
        position
    )

    current_tp = position_tp(
        position
    )

    initial_sl, desired_tp = (
        initial_sl_tp(
            side,
            open_price,
            tick,
            digits,
        )
    )

    be_sl, be_stage = desired_be_sl(
        side,
        open_price,
        current_price,
        tick,
        digits,
    )

    desired_sl = initial_sl

    if be_sl is not None:
        desired_sl = be_sl

    # Nikdy neposunieme uz lepsi SL spat horsim smerom.
    if current_sl is not None:
        if side == "BUY":
            desired_sl = max(
                desired_sl,
                current_sl,
            )

        else:
            desired_sl = min(
                desired_sl,
                current_sl,
            )

    desired_sl = normalize_price(
        desired_sl,
        tick,
        digits,
    )

    desired_tp = normalize_price(
        desired_tp,
        tick,
        digits,
    )

    sl_missing = (
        current_sl is None
        or current_sl == 0
    )

    tp_missing = (
        current_tp is None
        or current_tp == 0
    )

    sl_diff = (
        sl_missing
        or abs(
            float(current_sl)
            - desired_sl
        ) >= tick / 2
    )

    tp_diff = (
        tp_missing
        or abs(
            float(current_tp)
            - desired_tp
        ) >= tick / 2
    )

    if not sl_diff and not tp_diff:
        return

    ok = await modify_position(
        connection,
        pos_id,
        desired_sl,
        desired_tp,
    )

    if not ok:
        return

    print(
        f"POSITION PROTECTED "
        f"{side} "
        f"SL={desired_sl:.{digits}f} "
        f"TP={desired_tp:.{digits}f} "
        f"{be_stage or 'INITIAL'}",
        flush=True,
    )

    if be_stage:
        telegram(
            f"RIObot GOLD {be_stage}\n\n"
            f"{side}\n"
            f"Open: {open_price:.{digits}f}\n"
            f"SL locked: {desired_sl:.{digits}f}\n"
            f"TP: {desired_tp:.{digits}f}"
        )


# =========================================================
# PROTECT ALL OPEN POSITIONS
# =========================================================

async def protect_open_positions(
    connection,
):
    positions = await get_positions(
        connection
    )

    for position in positions:
        await protect_position(
            connection,
            position,
        )

    return positions
    # =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(
    connection,
    side,
):
    positions = await get_positions(
        connection
    )

    if positions:
        print(
            "ENTRY BLOCKED: "
            "XAUUSD POSITION ALREADY OPEN",
            flush=True,
        )
        return False

    tick, digits, bid, ask = (
        await get_market(connection)
    )

    entry = (
        ask
        if side == "BUY"
        else bid
    )

    sl, tp = initial_sl_tp(
        side,
        entry,
        tick,
        digits,
    )

    options = {
        "comment": COMMENT,
    }

    try:
        if side == "BUY":
            result = await asyncio.wait_for(
                connection.create_market_buy_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    options,
                ),
                timeout=META_TIMEOUT,
            )

        else:
            result = await asyncio.wait_for(
                connection.create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    options,
                ),
                timeout=META_TIMEOUT,
            )

        print(
            f"ORDER OK {side}: "
            f"{result}",
            flush=True,
        )

        telegram(
            f"RIObot GOLD ORDER OK\n\n"
            f"{side} {LOT_SIZE}\n"
            f"Entry approx: "
            f"{entry:.{digits}f}\n"
            f"SL: {sl:.{digits}f}\n"
            f"TP: {tp:.{digits}f}"
        )

        return True

    except Exception as exc:
        print(
            f"ORDER ERROR {side}: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True,
        )

        telegram(
            f"RIObot GOLD ORDER ERROR\n\n"
            f"{side}\n"
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return False


# =========================================================
# NEWS FILTER
# =========================================================

def parse_news_time(value):
    if not value:
        return None

    try:
        ts = pd.to_datetime(
            value,
            utc=True,
            errors="coerce",
        )

        if pd.isna(ts):
            return None

        return ts.to_pydatetime()

    except Exception:
        return None


def is_gold_news_event(event):
    currency = str(
        event.get("country")
        or event.get("currency")
        or ""
    ).upper()

    impact = str(
        event.get("impact")
        or ""
    ).lower()

    if currency != "USD":
        return False

    return (
        "high" in impact
        or "red" in impact
    )


def fetch_news_events():
    response = requests.get(
        NEWS_URL,
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"NEWS HTTP "
            f"{response.status_code}: "
            f"{response.text[:250]}"
        )

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "NEWS RESPONSE IS NOT A LIST"
        )

    events = []

    for item in data:
        if not isinstance(item, dict):
            continue

        if not is_gold_news_event(item):
            continue

        event_time = parse_news_time(
            item.get("date")
        )

        if event_time is None:
            continue

        title = str(
            item.get("title")
            or "USD HIGH IMPACT NEWS"
        )

        events.append(
            {
                "time": event_time,
                "title": title,
            }
        )

    events.sort(
        key=lambda x: x["time"]
    )

    return events


async def refresh_news(state):
    now = time.time()

    if now < state["news_next_fetch"]:
        return

    state["news_next_fetch"] = (
        now + NEWS_FETCH_SECONDS
    )

    try:
        events = await asyncio.to_thread(
            fetch_news_events
        )

        state["news_events"] = events
        state["news_last_success"] = now
        state["news_error_notified"] = False

        print(
            f"NEWS READY: "
            f"{len(events)} "
            f"USD HIGH IMPACT EVENTS",
            flush=True,
        )

    except Exception as exc:
        print(
            f"NEWS WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True,
        )

        if not state[
            "news_error_notified"
        ]:
            telegram(
                "RIObot GOLD NEWS WARNING\n\n"
                "News calendar could not "
                "be refreshed.\n"
                "New entries are blocked "
                "until news data is safe."
            )

            state[
                "news_error_notified"
            ] = True


def news_block_reason(state):
    now_ts = time.time()

    last_success = state.get(
        "news_last_success",
        0.0,
    )

    if (
        last_success <= 0
        or now_ts - last_success
        > NEWS_STALE_SECONDS
    ):
        return (
            "NEWS DATA MISSING/STALE"
        )

    now = datetime.now().astimezone()

    for event in state["news_events"]:
        event_time = event["time"]

        if event_time.tzinfo is None:
            continue

        local_event = (
            event_time.astimezone()
        )

        seconds = (
            local_event - now
        ).total_seconds()

        before = (
            NEWS_BEFORE_MINUTES * 60
        )

        after = (
            NEWS_AFTER_MINUTES * 60
        )

        if (
            -after
            <= seconds
            <= before
        ):
            return (
                f"{event['title']} "
                f"{local_event.strftime('%H:%M')}"
            )

    return None


# =========================================================
# SETUP STATE
# =========================================================

def clear_setup(state):
    state["armed"] = False
    state["setup_phase"] = None
    state["setup_side"] = None
    state["setup_key"] = None
    state["setup_level"] = None
    state["setup_started"] = 0.0
    state["setup_break_time"] = 0.0
    state["setup_break_price"] = None
    state["setup_last_price"] = None
    state["setup_params"] = None
    state["retest_seen"] = False


def arm_setup(
    state,
    side,
    signal_key,
    level,
    params,
):
    state["armed"] = True
    state["setup_phase"] = "WAIT_BREAK"
    state["setup_side"] = side
    state["setup_key"] = signal_key
    state["setup_level"] = float(level)
    state["setup_started"] = time.time()
    state["setup_break_time"] = 0.0
    state["setup_break_price"] = None
    state["setup_last_price"] = None
    state["setup_params"] = params
    state["retest_seen"] = False

    print(
        f"SETUP ARMED {side} "
        f"LEVEL={level:.2f}",
        flush=True,
    )


def setup_expired(state):
    if not state["armed"]:
        return False

    started = state.get(
        "setup_started",
        0.0,
    )

    if started <= 0:
        return True

    return (
        time.time() - started
        > SETUP_EXPIRY_SECONDS
        )
    def retest_expired(state):
    if not state["armed"]:
        return False

    if state["setup_phase"] != "WAIT_RETEST":
        return False

    break_time = state.get(
        "setup_break_time",
        0.0,
    )

    if break_time <= 0:
        return False

    return (
        time.time() - break_time
        > RETEST_EXPIRY_SECONDS
    )


# =========================================================
# SETUP MANAGEMENT
# =========================================================

async def manage_setup(
    state,
    connection,
    df1,
    df5,
):
    if not state["armed"]:
        return

    if setup_expired(state):
        print(
            "SETUP EXPIRED",
            flush=True,
        )

        clear_setup(state)
        return

    if retest_expired(state):
        print(
            "RETEST EXPIRED",
            flush=True,
        )

        clear_setup(state)
        return

    side = state["setup_side"]
    level = float(
        state["setup_level"]
    )

    params = state.get(
        "setup_params"
    )

    if not params:
        clear_setup(state)
        return

    # Stale setup sa nesmie pouzit,
    # ak uz PSAR smer nesedi.
    if current_psar_side(df1) != side:
        print(
            f"SETUP CANCELLED {side}: "
            f"M1 PSAR CHANGED",
            flush=True,
        )

        clear_setup(state)
        return

    if not m5_confirms(
        df5,
        side,
    ):
        print(
            f"SETUP CANCELLED {side}: "
            f"M5 CONFIRM LOST",
            flush=True,
        )

        clear_setup(state)
        return

    if not trend_filter_ok(
        df5,
        side,
    ):
        print(
            f"SETUP CANCELLED {side}: "
            f"EMA/ADX FILTER LOST",
            flush=True,
        )

        clear_setup(state)
        return

    news_reason = news_block_reason(
        state
    )

    if news_reason:
        print(
            f"SETUP CANCELLED {side}: "
            f"NEWS BLOCK "
            f"{news_reason}",
            flush=True,
        )

        clear_setup(state)
        return

    positions = await get_positions(
        connection
    )

    if positions:
        clear_setup(state)
        return

    _, _, bid, ask = (
        await get_market(connection)
    )

    current_price = (
        ask
        if side == "BUY"
        else bid
    )

    phase = state["setup_phase"]

    max_chase = float(
        params["max_chase"]
    )

    retest = float(
        params["retest"]
    )

    invalidate = float(
        params["invalidate"]
    )

    confirm = float(
        params["confirm"]
    )

    extension = float(
        params["extension"]
    )

    # -----------------------------------------------------
    # WAIT FOR BREAKOUT
    # -----------------------------------------------------

    if phase == "WAIT_BREAK":
        if big_impulse(df1):
            print(
                f"WAIT {side}: "
                f"BIG IMPULSE - NO CHASE",
                flush=True,
            )

            state[
                "setup_last_price"
            ] = current_price

            return

        if side == "BUY":
            distance = (
                current_price - level
            )

            if distance > max_chase:
                print(
                    f"SETUP CANCELLED BUY: "
                    f"CHASE "
                    f"{distance:.2f}",
                    flush=True,
                )

                clear_setup(state)
                return

            if current_price > level:
                state[
                    "setup_phase"
                ] = "WAIT_RETEST"

                state[
                    "setup_break_time"
                ] = time.time()

                state[
                    "setup_break_price"
                ] = current_price

                print(
                    f"BREAKOUT BUY "
                    f"{current_price:.2f} "
                    f"> {level:.2f}",
                    flush=True,
                )

        else:
            distance = (
                level - current_price
            )

            if distance > max_chase:
                print(
                    f"SETUP CANCELLED SELL: "
                    f"CHASE "
                    f"{distance:.2f}",
                    flush=True,
                )

                clear_setup(state)
                return

            if current_price < level:
                state[
                    "setup_phase"
                ] = "WAIT_RETEST"

                state[
                    "setup_break_time"
                ] = time.time()

                state[
                    "setup_break_price"
                ] = current_price

                print(
                    f"BREAKOUT SELL "
                    f"{current_price:.2f} "
                    f"< {level:.2f}",
                    flush=True,
                )

        state[
            "setup_last_price"
        ] = current_price

        return

    # -----------------------------------------------------
    # WAIT FOR RETEST + CONFIRMATION
    # -----------------------------------------------------

    if phase == "WAIT_RETEST":
        break_price = state.get(
            "setup_break_price"
        )

        if break_price is None:
            clear_setup(state)
            return

        if side == "BUY":
            if (
                current_price
                < level - invalidate
            ):
                print(
                    "SETUP CANCELLED BUY: "
                    "BREAKOUT FAILED",
                    flush=True,
                )

                clear_setup(state)
                return

            if (
                current_price
                <= level + retest
            ):
                state[
                    "retest_seen"
                ] = True

            if not state[
                "retest_seen"
            ]:
                if (
                    current_price
                    > break_price + extension
                ):
                    print(
                        "SETUP CANCELLED BUY: "
                        "NO RETEST / CHASE",
                        flush=True,
                    )

                    clear_setup(state)
                    return

                state[
                    "setup_last_price"
                ] = current_price

                return

            if (
                current_price
                >= level + confirm
            ):
                state[
                    "setup_phase"
                ] = "READY"

        else:
            if (
                current_price
                > level + invalidate
            ):
                print(
                    "SETUP CANCELLED SELL: "
                    "BREAKOUT FAILED",
                    flush=True,
                )

                clear_setup(state)
                return

            if (
                current_price
                >= level - retest
            ):
                state[
                    "retest_seen"
                ] = True

            if not state[
                "retest_seen"
            ]:
                if (
                    current_price
                    < break_price - extension
                ):
                    print(
                        "SETUP CANCELLED SELL: "
                        "NO RETEST / CHASE",
                        flush=True,
                    )

                    clear_setup(state)
                    return

                state[
                    "setup_last_price"
                ] = current_price

                return

            if (
                current_price
                <= level - confirm
            ):
                state[
                    "setup_phase"
                ] = "READY"

        state[
            "setup_last_price"
        ] = current_price

        if state["setup_phase"] != "READY":
            return

    # -----------------------------------------------------
    # FINAL ENTRY CHECK
    # -----------------------------------------------------

    if state["setup_phase"] == "READY":
        news_again = news_block_reason(
            state
        )

        positions_again = (
            await get_positions(
                connection
            )
        )

        blocked_again = (
            news_again is not None
        )

        filters_still_ok = (
            current_psar_side(df1)
            == side
            and m5_confirms(
                df5,
                side,
            )
            and trend_filter_ok(
                df5,
                side,
            )
        )

        # NOVY M1 FILTER:
        # 2 uzavrete M1 sviecky proti vstupu
        # obchod zablokuju.
        m1_pressure_ok = (
            m1_entry_pressure_ok(
                df1,
                side,
            )
        )

        if not m1_pressure_ok:
            print(
                f"ENTRY BLOCKED {side}: "
                "2 CLOSED M1 CANDLES "
                "PRESS AGAINST ENTRY",
                flush=True,
            )

        if (
            not blocked_again
            and not positions_again
            and filters_still_ok
            and m1_pressure_ok
        ):
            used_key = state.get(
                "setup_key"
            )

            print(
                f"ENTRY CONFIRMED "
                f"{side} "
                f"LEVEL="
                f"{level:.2f} "
                f"PRICE="
                f"{current_price:.2f}",
                flush=True,
            )

            clear_setup(state)

            opened = await open_trade(
                connection,
                side,
            )

            if opened:
                state[
                    "last_signal_key"
                ] = used_key

                state[
                    "require_new_flip"
                ] = True

            return

        # Ak M1 filter vstup zablokoval,
        # tento setup uz nepouzijeme neskor.
        if not m1_pressure_ok:
            clear_setup(state)
            return

        if blocked_again:
            print(
                f"ENTRY BLOCKED {side}: "
                f"NEWS "
                f"{news_again}",
                flush=True,
            )

        elif positions_again:
            print(
                f"ENTRY BLOCKED {side}: "
                "POSITION EXISTS",
                flush=True,
            )

        elif not filters_still_ok:
            print(
                f"ENTRY BLOCKED {side}: "
                "FILTER CHANGED",
                flush=True,
            )

        clear_setup(state)
        # =========================================================
# PENDING SIGNAL
# =========================================================

def clear_pending(state):
    state["pending_signal_key"] = None
    state["pending_signal_side"] = None
    state["pending_started"] = 0.0


def signal_key(df1, side):
    if df1 is None or df1.empty:
        return None

    return (
        f"{side}:"
        f"{str(df1.iloc[-1]['time'])}"
    )


def breakout_level_for_signal(
    df1,
    side,
):
    if df1 is None or len(df1) < 3:
        return None

    # Pouzijeme uzavrete M1 sviecky
    # pred aktualnou live svieckou.
    closed = df1.iloc[:-1].tail(3)

    if closed.empty:
        return None

    if side == "BUY":
        return float(
            closed["high"].max()
        )

    if side == "SELL":
        return float(
            closed["low"].min()
        )

    return None


# =========================================================
# BOT SESSION
# =========================================================

async def bot_session(state):
    api = MetaApi(M_TOKEN)

    account = await asyncio.wait_for(
        api.metatrader_account_api.get_account(
            M_ACC
        ),
        timeout=META_TIMEOUT,
    )

    region = (
        getattr(account, "region", None)
        or DEFAULT_META_REGION
    )

    state["region"] = region

    if str(account.state).upper() != "DEPLOYED":
        print(
            "DEPLOYING METAAPI ACCOUNT...",
            flush=True,
        )

        await asyncio.wait_for(
            account.deploy(),
            timeout=META_TIMEOUT,
        )

    await asyncio.wait_for(
        account.wait_connected(),
        timeout=META_TIMEOUT,
    )

    connection = (
        account.get_rpc_connection()
    )

    try:
        await asyncio.wait_for(
            connection.connect(),
            timeout=META_TIMEOUT,
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=META_TIMEOUT,
        )

        reconnect = state[
            "ever_connected"
        ]

        state[
            "ever_connected"
        ] = True

        print(
            "RECONNECTED"
            if reconnect
            else "CONNECTED",
            flush=True,
        )

        telegram(
            "RIObot GOLD RECONNECTED"
            if reconnect
            else "RIObot GOLD START / CONNECTED"
        )

        # =================================================
        # DOLEZITE:
        # Po pripojeni / reconnecte necakame
        # na dalsi bezny cyklus.
        #
        # HNED:
        # 1. nacitame otvorenu poziciu z MT5
        # 2. obnovime SL / TP ak treba
        # 3. skontrolujeme BE1 / BE2 / BE3
        # =================================================

        try:
            positions_now = (
                await protect_open_positions(
                    connection
                )
            )

            state["had_position"] = bool(
                positions_now
            )

            if positions_now:
                state[
                    "require_new_flip"
                ] = True

                clear_pending(state)
                clear_setup(state)

                print(
                    "OPEN POSITION RESTORED "
                    "IMMEDIATELY AFTER CONNECT",
                    flush=True,
                )

        except Exception as exc:
            print(
                "IMMEDIATE POSITION CHECK "
                f"WARNING: "
                f"{type(exc).__name__}: "
                f"{exc}",
                flush=True,
            )

        # Nacitanie historie po pripojeni
        await seed_history(state)

        loop_errors = 0

        while True:
            try:
                # -----------------------------------------
                # NEWS REFRESH
                # -----------------------------------------

                await refresh_news(state)

                # -----------------------------------------
                # LIVE M1
                # -----------------------------------------

                candle = await get_current_m1(
                    state["region"]
                )

                if candle is None:
                    raise RuntimeError(
                        "CURRENT M1 CANDLE MISSING"
                    )

                update_cache(
                    state["m1_candles"],
                    candle,
                )

                df1 = make_m1_df(
                    state["m1_candles"]
                )

                df5 = make_m5_df(
                    state["m1_candles"]
                )

                if (
                    df1 is None
                    or df5 is None
                ):
                    print(
                        "WAITING FOR ENOUGH "
                        "M1/M5 DATA",
                        flush=True,
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # -----------------------------------------
                # OTVORENA POZICIA:
                # BE + SL/TP kontrola kazdy cyklus
                # -----------------------------------------

                positions = (
                    await protect_open_positions(
                        connection
                    )
                )

                if positions:
                    state["had_position"] = True
                    state[
                        "require_new_flip"
                    ] = True

                    clear_pending(state)
                    clear_setup(state)

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # -----------------------------------------
                # POZICIA SA PRAVE ZATVORILA
                # -----------------------------------------

                if state["had_position"]:
                    state["had_position"] = False
                    state[
                        "require_new_flip"
                    ] = True

                    clear_pending(state)
                    clear_setup(state)

                    print(
                        "POSITION CLOSED - "
                        "WAITING FOR NEW PSAR FLIP",
                        flush=True,
                    )

                # -----------------------------------------
                # AKTUALNY LIVE PSAR FLIP
                # -----------------------------------------

                signal = get_live_flip(df1)

                current_side = (
                    current_psar_side(df1)
                )

                # Po obchode musi prist novy flip.
                if state[
                    "require_new_flip"
                ]:
                    if signal is None:
                        loop_errors = 0

                        await asyncio.sleep(
                            LOOP_SECONDS
                        )

                        continue

                    state[
                        "require_new_flip"
                    ] = False

                # -----------------------------------------
                # NOVY SIGNAL -> 10s CONFIRM
                # -----------------------------------------

                if signal:
                    key = signal_key(
                        df1,
                        signal,
                    )

                    if (
                        key
                        and key
                        != state.get(
                            "last_signal_key"
                        )
                        and key
                        != state.get(
                            "pending_signal_key"
                        )
                    ):
                        state[
                            "pending_signal_key"
                        ] = key

                        state[
                            "pending_signal_side"
                        ] = signal

                        state[
                            "pending_started"
                        ] = time.time()

                        print(
                            f"M1 LIVE FLIP "
                            f"{signal} - "
                            f"10s CONFIRM START",
                            flush=True,
                        )

                # -----------------------------------------
                # PENDING SIGNAL CHECK
                # -----------------------------------------

                pending_key = state.get(
                    "pending_signal_key"
                )

                pending_side = state.get(
                    "pending_signal_side"
                )

                pending_started = float(
                    state.get(
                        "pending_started",
                        0.0,
                    )
                    or 0.0
                )

                if (
                    pending_key
                    and pending_side
                ):
                    # Ak sa PSAR medzitym vratil,
                    # signal rusime.
                    if current_side != pending_side:
                        print(
                            f"M1 SIGNAL CANCELLED "
                            f"{pending_side}: "
                            f"PSAR DID NOT HOLD",
                            flush=True,
                        )

                        clear_pending(state)

                    elif (
                        time.time()
                        - pending_started
                        >= SIGNAL_CONFIRM_SECONDS
                    ):
                        # 10 sekund PSAR vydrzal.
                        # Teraz M5 + EMA50 + ADX.

                        if (
                            m5_confirms(
                                df5,
                                pending_side,
                            )
                            and trend_filter_ok(
                                df5,
                                pending_side,
                            )
                        ):
                            news_reason = (
                                news_block_reason(
                                    state
                                )
                            )

                            if news_reason:
                                print(
                                    f"SIGNAL BLOCKED "
                                    f"{pending_side}: "
                                    f"NEWS "
                                    f"{news_reason}",
                                    flush=True,
                                )

                                clear_pending(
                                    state
                                )

                            else:
                                breakout_level = (
                                    breakout_level_for_signal(
                                        df1,
                                        pending_side,
                                    )
                                )

                                if (
                                    breakout_level
                                    is not None
                                ):
                                    params = (
                                        params_for_setup(
                                            df1
                                        )
                                    )

                                    used_key = (
                                        pending_key
                                    )

                                    used_side = (
                                        pending_side
                                    )

                                    clear_pending(
                                        state
                                    )

                                    arm_setup(
                                        state,
                                        used_side,
                                        used_key,
                                        breakout_level,
                                        params,
                                    )

                                    telegram(
                                        "RIObot GOLD "
                                        "SETUP ARMED\n\n"
                                        f"{used_side} "
                                        f"{SYMBOL}\n"
                                        f"Level: "
                                        f"{breakout_level:.2f}\n"
                                        "Waiting for breakout "
                                        "-> retest -> "
                                        "confirmation."
                                    )

                        else:
                            print(
                                f"M5/EMA/ADX "
                                f"NOT CONFIRMED "
                                f"{pending_side}",
                                flush=True,
                            )

                            clear_pending(state)

                # -----------------------------------------
                # AK JE SETUP ARMED, SPRACUJ HO
                # -----------------------------------------

                if state.get("armed"):
                    await manage_setup(
                        state,
                        connection,
                        df1,
                        df5,
                    )

                loop_errors = 0

            except Exception as exc:
                loop_errors += 1

                print(
                    f"LOOP WARNING "
                    f"{loop_errors}/"
                    f"{MAX_LOOP_ERRORS}: "
                    f"{type(exc).__name__}: "
                    f"{exc}",
                    flush=True,
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
        try:
            await asyncio.wait_for(
                connection.close(),
                timeout=10,
            )

        except Exception:
            pass
            # =========================================================
# MAIN
# =========================================================

async def main():
    keep_alive()

    print(
        f"TELEGRAM ENV: "
        f"T_TOKEN={'OK' if T_TOKEN else 'MISSING'} | "
        f"T_CHAT={'OK' if T_CHAT else 'MISSING'}",
        flush=True,
    )

    telegram(
        "RIObot GOLD TELEGRAM TEST\n\n"
        "Telegram funguje.\n"
        "RIObot GOLD sa spustil."
    )

    if not M_TOKEN:
        raise RuntimeError(
            "M_TOKEN is missing"
        )

    if not M_ACC:
        raise RuntimeError(
            "M_ACC is missing"
        )

    state = {
        "ever_connected": False,
        "region": DEFAULT_META_REGION,

        "m1_candles": load_cache(),
        "history_seeded": False,

        "had_position": False,
        "require_new_flip": False,
        "last_signal_key": None,

        "pending_signal_key": None,
        "pending_signal_side": None,
        "pending_started": 0.0,

        "armed": False,
        "setup_phase": None,
        "setup_side": None,
        "setup_key": None,
        "setup_level": None,
        "setup_started": 0.0,
        "setup_break_time": 0.0,
        "setup_break_price": None,
        "setup_last_price": None,
        "setup_params": None,
        "retest_seen": False,

        "news_events": [],
        "news_last_success": 0.0,
        "news_next_fetch": 0.0,
        "news_error_notified": False,
        "news_block_notified": None,
    }

    while True:
        try:
            await bot_session(state)

        except Exception as exc:
            print(
                f"BOT SESSION ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}",
                flush=True,
            )

            telegram(
                "RIObot GOLD CONNECTION ERROR\n\n"
                f"Reconnect in "
                f"{RECONNECT_SECONDS} seconds."
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
