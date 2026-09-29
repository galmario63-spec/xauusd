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
# RIObot GOLD - FAST ENTRY
# 2x M1 PSAR + LIVE M5 + EMA50/ADX
# =========================================================

SYMBOL = "XAUUSD"

LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

# TP +5 / SL -8
TP_DISTANCE = 6.00
SL_DISTANCE = 8.00

# BE: pri +3 -> zamkne +1
BE_TRIGGER = 4.00
BE_LOCK = 2.00

EMA_PERIOD = 50
ADX_PERIOD = 14
ADX_MIN = 20.0

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

COMMENT = "RIO FAST 2M1 LIVE M5 EMA50 ADX"


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

        return response.status_code == 200

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
    if not c:
        return None

    if not all(
        k in c
        for k in ("time", "open", "high", "low", "close")
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
            f"CACHE LOAD WARNING: {exc}",
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
            f"CACHE SAVE WARNING: {exc}",
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
                f"M1 HTTP {response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:
        raw = await asyncio.to_thread(fetch)
        return clean_candle(raw)

    except Exception as exc:
        print(
            f"M1 CANDLE WARNING: {exc}",
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
                f"HISTORY HTTP {response.status_code}: "
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
            f"HISTORY WARNING: {exc}",
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
            f"M5={len(df5)}",
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


# 2 M1 PSAR bodky v rovnakom smere
def two_m1_psar_dots(df1, side):
    if df1 is None or len(df1) < 2:
        return False

    rows = df1.iloc[-2:]

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


# RYCHLE M5 POTVRDENIE
# staci aktualny LIVE M5 PSAR smer
def m5_confirms(df5, side):
    if df5 is None or df5.empty:
        return False

    row = df5.iloc[-1]

    if side == "BUY":
        return (
            float(row["psar"])
            < float(row["close"])
        )

    if side == "SELL":
        return (
            float(row["psar"])
            > float(row["close"])
        )

    return False


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


def normalize_price(value, tick, digits):
    if tick <= 0:
        return round(value, digits)

    steps = round(
        value / tick
    )

    return round(
        steps * tick,
        digits,
    )


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
        "stop_loss",
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
        "take_profit",
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
        "position_id",
    ):
        value = position.get(key)

        if value is not None:
            return str(value)

    return None


# =========================================================
# SL / TP
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


# =========================================================
# BREAK EVEN
# pri +3 -> SL zamkne +1
# =========================================================

def desired_be_sl(
    side,
    open_price,
    current_price,
    tick,
    digits,
):
    if side == "BUY":
        profit = current_price - open_price

        if profit >= BE_TRIGGER:
            return normalize_price(
                open_price + BE_LOCK,
                tick,
                digits,
            )

    if side == "SELL":
        profit = open_price - current_price

        if profit >= BE_TRIGGER:
            return normalize_price(
                open_price - BE_LOCK,
                tick,
                digits,
            )

    return None


async def modify_position(
    connection,
    pid,
    sl,
    tp,
):
    await asyncio.wait_for(
        connection.modify_position(
            pid,
            sl,
            tp,
        ),
        timeout=META_TIMEOUT,
    )


async def protect_position(
    connection,
    position,
):
    side = position_side(position)
    open_price = position_open_price(position)
    pid = position_id(position)

    if (
        side is None
        or open_price is None
        or pid is None
    ):
        return

    tick, digits, bid, ask = (
        await get_market(connection)
    )

    current_price = (
        bid
        if side == "BUY"
        else ask
    )

    current_sl = position_sl(position)
    current_tp = position_tp(position)

    initial_sl, initial_tp = (
        initial_sl_tp(
            side,
            open_price,
            tick,
            digits,
        )
    )

    wanted_sl = current_sl
    wanted_tp = current_tp

    if wanted_sl is None:
        wanted_sl = initial_sl

    if wanted_tp is None:
        wanted_tp = initial_tp

    be_sl = desired_be_sl(
        side,
        open_price,
        current_price,
        tick,
        digits,
    )

    if be_sl is not None:
        if side == "BUY":
            if (
                wanted_sl is None
                or be_sl > wanted_sl
            ):
                wanted_sl = be_sl

        else:
            if (
                wanted_sl is None
                or be_sl < wanted_sl
            ):
                wanted_sl = be_sl

    change_needed = False

    if current_sl is None:
        change_needed = True

    elif (
        wanted_sl is not None
        and abs(
            current_sl - wanted_sl
        ) >= tick / 2
    ):
        change_needed = True

    if current_tp is None:
        change_needed = True

    elif (
        wanted_tp is not None
        and abs(
            current_tp - wanted_tp
        ) >= tick / 2
    ):
        change_needed = True

    if change_needed:
        try:
            await modify_position(
                connection,
                pid,
                wanted_sl,
                wanted_tp,
            )

            print(
                f"POSITION PROTECTED "
                f"{side} "
                f"SL={wanted_sl} "
                f"TP={wanted_tp}",
                flush=True,
            )

        except Exception as exc:
            print(
                f"PROTECT WARNING: {exc}",
                flush=True,
            )


async def protect_open_positions(
    connection
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
    # Maximalne 1 otvoreny XAUUSD obchod
    positions = await get_positions(
        connection
    )

    if positions:
        print(
            "ENTRY BLOCKED: POSITION ALREADY OPEN",
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
        "comment": COMMENT
    }

    try:
        if side == "BUY":
            await asyncio.wait_for(
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
            await asyncio.wait_for(
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
            f"ORDER OK {side} "
            f"{SYMBOL} "
            f"LOT={LOT_SIZE} "
            f"SL={sl} TP={tp}",
            flush=True,
        )

        telegram(
            f"RIObot GOLD ORDER OK\n\n"
            f"{side} {SYMBOL}\n"
            f"LOT: {LOT_SIZE}\n"
            f"SL: {sl}\n"
            f"TP: {tp}"
        )

        return True

    except Exception as exc:
        print(
            f"ORDER ERROR {side}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        telegram(
            f"RIObot GOLD ORDER ERROR\n\n"
            f"{side} {SYMBOL}\n"
            f"{type(exc).__name__}: {exc}"
        )

        return False


# =========================================================
# NEWS
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
            f"NEWS HTTP {response.status_code}"
        )

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "NEWS RESPONSE IS NOT A LIST"
        )

    events = []

    for event in data:
        if not isinstance(event, dict):
            continue

        if not is_gold_news_event(event):
            continue

        event_time = parse_news_time(
            event.get("date")
            or event.get("time")
        )

        if event_time is None:
            continue

        events.append(
            {
                "time": event_time,
                "title": str(
                    event.get("title")
                    or event.get("event")
                    or "USD NEWS"
                ),
            }
        )

    return events


async def refresh_news(state):
    now = time.time()

    if now < state.get(
        "news_next_fetch",
        0.0,
    ):
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
            f"NEWS READY: {len(events)} "
            f"USD HIGH IMPACT EVENTS",
            flush=True,
        )

    except Exception as exc:
        print(
            f"NEWS WARNING: {exc}",
            flush=True,
        )

        if not state.get(
            "news_error_notified"
        ):
            telegram(
                "RIObot GOLD NEWS WARNING\n\n"
                "Economic calendar could not be refreshed."
            )

            state[
                "news_error_notified"
            ] = True


def news_block_reason(state):
    now = datetime.now().astimezone()

    last_success = float(
        state.get(
            "news_last_success",
            0.0,
        )
        or 0.0
    )

    if (
        last_success <= 0
        or time.time() - last_success
        > NEWS_STALE_SECONDS
    ):
        return "NEWS DATA NOT READY/STALE"

    for event in state.get(
        "news_events",
        []
    ):
        event_time = event.get("time")

        if event_time is None:
            continue

        try:
            delta_minutes = (
                event_time.timestamp()
                - now.timestamp()
            ) / 60.0

        except Exception:
            continue

        if (
            -NEWS_AFTER_MINUTES
            <= delta_minutes
            <= NEWS_BEFORE_MINUTES
        ):
            return (
                f"{event.get('title')} "
                f"{delta_minutes:+.1f} min"
            )

    return None


# =========================================================
# STATE HELPERS
# =========================================================

def clear_pending(state):
    state["pending_signal_key"] = None
    state["pending_signal_side"] = None
    state["pending_started"] = 0.0


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

        # Hned po connect/reconnect
        # skontroluj otvorenu poziciu a BE

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
                f"WARNING: {exc}",
                flush=True,
            )

        await seed_history(state)

        loop_errors = 0

        while True:
            try:

                # -----------------------------------------
                # NEWS
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
                        "WAITING FOR ENOUGH M1/M5 DATA",
                        flush=True,
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # -----------------------------------------
                # OTVORENY OBCHOD
                # -----------------------------------------

                positions = (
                    await protect_open_positions(
                        connection
                    )
                )

                if positions:
                    state[
                        "had_position"
                    ] = True

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
                # OBCHOD SA ZATVORIL
                # -----------------------------------------

                if state["had_position"]:
                    state[
                        "had_position"
                    ] = False

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
                # FAST ENTRY
                # 2x M1 PSAR
                # + LIVE M5 PSAR
                # + EMA50/ADX/DI
                # -----------------------------------------

                signal = get_live_flip(df1)

                current_side = (
                    current_psar_side(df1)
                )

                # Po predchadzajucom obchode
                # musi prist novy M1 PSAR flip.
                # Potom uz caka iba na 2 M1 bodky
                # + LIVE M5 potvrdenie.

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

                    print(
                        f"NEW PSAR FLIP: "
                        f"{signal}",
                        flush=True,
                    )

                # RYCHLY VSTUP:
                # 2x M1 PSAR
                # + aktualny LIVE M5 PSAR
                # + EMA50
                # + ADX
                # + DI smer
                #
                # BEZ:
                # M1 pressure filtra
                # impulse filtra
                # 2 M5 PSAR bodiek

                if (
                    current_side
                    in ("BUY", "SELL")
                    and two_m1_psar_dots(
                        df1,
                        current_side,
                    )
                    and m5_confirms(
                        df5,
                        current_side,
                    )
                    and trend_filter_ok(
                        df5,
                        current_side,
                    )
                ):
                    news_reason = (
                        news_block_reason(
                            state
                        )
                    )

                    if news_reason:
                        print(
                            f"ENTRY BLOCKED "
                            f"{current_side}: "
                            f"NEWS {news_reason}",
                            flush=True,
                        )

                    else:
                        positions_again = (
                            await get_positions(
                                connection
                            )
                        )

                        if not positions_again:
                            print(
                                f"FAST ENTRY "
                                f"{current_side}: "
                                f"2x M1 PSAR + "
                                f"LIVE M5 + "
                                f"EMA/ADX CONFIRMED",
                                flush=True,
                            )

                            opened = (
                                await open_trade(
                                    connection,
                                    current_side,
                                )
                            )

                            if opened:
                                state[
                                    "had_position"
                                ] = True

                                state[
                                    "require_new_flip"
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
