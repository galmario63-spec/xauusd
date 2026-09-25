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
        print(f"TELEGRAM WARNING: {type(exc).__name__}: {exc}", flush=True)


# =========================================================
# CACHE
# =========================================================

def clean_candle(c):
    if (
        not c
        or not all(k in c for k in ("time", "open", "high", "low", "close"))
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

        out = [clean_candle(c) for c in data[-MAX_CACHE_BARS:]]
        out = [c for c in out if c]
        out.sort(key=lambda x: x["time"])
        return out

    except Exception as exc:
        print(f"CACHE LOAD WARNING: {type(exc).__name__}: {exc}", flush=True)
        return []


def save_cache(candles):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(candles[-MAX_CACHE_BARS:], f)
    except Exception as exc:
        print(f"CACHE SAVE WARNING: {type(exc).__name__}: {exc}", flush=True)


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
            headers={"Accept": "application/json", "auth-token": M_TOKEN},
            timeout=20,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"M1 candle HTTP {response.status_code}: {response.text[:250]}"
            )
        return response.json()

    try:
        raw = await asyncio.to_thread(fetch)
        return clean_candle(raw)
    except Exception as exc:
        print(f"M1 CANDLE WARNING: {type(exc).__name__}: {exc}", flush=True)
        return None


async def get_history_m1(region):
    url = (
        f"https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/historical-market-data/"
        f"symbols/{SYMBOL}/timeframes/1m/candles?limit={HISTORY_SEED_BARS}"
    )

    def fetch():
        response = requests.get(
            url,
            headers={"Accept": "application/json", "auth-token": M_TOKEN},
            timeout=40,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"historical M1 HTTP {response.status_code}: {response.text[:250]}"
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
        print(f"HISTORY M1 WARNING: {type(exc).__name__}: {exc}", flush=True)
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
                sar = min(sar, lows[i - 1], lows[i - 2])
            else:
                sar = min(sar, lows[i - 1])

            if lows[i] < sar:
                bull = False
                sar = ep
                ep = lows[i]
                af = PSAR_STEP
            elif highs[i] > ep:
                ep = highs[i]
                af = min(af + PSAR_STEP, PSAR_MAX)
        else:
            if i >= 2:
                sar = max(sar, highs[i - 1], highs[i - 2])
            else:
                sar = max(sar, highs[i - 1])

            if highs[i] > sar:
                bull = True
                sar = ep
                ep = highs[i]
                af = PSAR_STEP
            elif lows[i] < ep:
                ep = lows[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

        psar[i] = sar

    return psar


# =========================================================
# DATAFRAMES
# =========================================================

def make_m1_df(candles):
    if len(candles) < MIN_PSAR_BARS:
        return None

    df = pd.DataFrame(candles)

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    if len(df) < MIN_PSAR_BARS:
        return None

    df["psar"] = psar_values(df)
    return df


def make_m5_df(m1_candles):
    if len(m1_candles) < 30:
        return None

    df = pd.DataFrame(m1_candles)

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["dt"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["dt", "open", "high", "low", "close"]).sort_values("dt")

    if df.empty:
        return None

    df["bucket"] = df["dt"].dt.floor("5min")

    m5 = (
        df.groupby("bucket", as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
        )
    )

    if len(m5) < MIN_PSAR_BARS:
        return None

    m5["time"] = m5["bucket"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    m5 = m5[["time", "open", "high", "low", "close"]].reset_index(drop=True)
    m5["psar"] = psar_values(m5)
    return m5


# =========================================================
# HISTORY SEED
# =========================================================

async def seed_history(state):
    if state["history_seeded"]:
        return

    history = await get_history_m1(state["region"])

    if history:
        state["m1_candles"] = history
        save_cache(history)

    df1 = make_m1_df(state["m1_candles"])
    df5 = make_m5_df(state["m1_candles"])

    state["history_seeded"] = df1 is not None and df5 is not None

    if state["history_seeded"]:
        print(
            f"HISTORY READY: M1={len(df1)} M5={len(df5)} (M5 derived from M1)",
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
        float(previous["psar"]) > float(previous["close"])
        and float(current["psar"]) < float(current["close"])
    ):
        return "BUY"

    if (
        float(previous["psar"]) < float(previous["close"])
        and float(current["psar"]) > float(current["close"])
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
            float(row["psar"]) < float(row["close"])
            for _, row in rows.iterrows()
        )

    if side == "SELL":
        return all(
            float(row["psar"]) > float(row["close"])
            for _, row in rows.iterrows()
        )

    return False


# =========================================================
# PRICE ACTION FILTERS
# =========================================================

def average_range(df1):
    if df1 is None or len(df1) < 4:
        return 0.0

    rows = df1.iloc[:-1].tail(IMPULSE_LOOKBACK)
    values = rows["high"].astype(float) - rows["low"].astype(float)
    values = values[values > 0]

    if values.empty:
        return 0.0

    return float(values.mean())


def big_impulse(df1):
    avg = average_range(df1)

    if avg <= 0:
        return False

    current = df1.iloc[-1]
    current_range = float(current["high"]) - float(current["low"])
    return current_range >= avg * IMPULSE_MULTIPLIER


def params_for_setup(df1):
    avg = average_range(df1) or 0.80
    return {
        "max_chase": min(max(avg * 0.90, 0.50), 1.80),
        "retest": min(max(avg * 0.30, 0.15), 0.55),
        "invalidate": min(max(avg * 0.55, 0.30), 1.00),
        "confirm": min(max(avg * 0.20, 0.10), 0.35),
        "extension": min(max(avg * 0.35, 0.20), 0.60),
    }


# =========================================================
# POSITIONS / MARKET
# =========================================================

async def get_positions(connection):
    positions = await asyncio.wait_for(connection.get_positions(), timeout=META_TIMEOUT)
    return [
        p for p in positions
        if str(p.get("symbol", "")).upper() == SYMBOL.upper()
    ]


async def get_market(connection):
    specification = await asyncio.wait_for(
        connection.get_symbol_specification(SYMBOL),
        timeout=META_TIMEOUT
    )
    price = await asyncio.wait_for(
        connection.get_symbol_price(SYMBOL),
        timeout=META_TIMEOUT
    )

    digits = int(specification.get("digits", 2))
    tick = float(specification.get("tickSize") or 10 ** (-digits))
    bid = float(price["bid"])
    ask = float(price["ask"])
    return tick, digits, bid, ask


# =========================================================
# SL / TP
# =========================================================

def levels(side, entry, digits):
    if side in ("BUY", "POSITION_TYPE_BUY"):
        return round(entry - SL_DISTANCE, digits), round(entry + TP_DISTANCE, digits)
    return round(entry + SL_DISTANCE, digits), round(entry - TP_DISTANCE, digits)


async def wait_position(connection, side, attempts=20):
    for _ in range(attempts):
        positions = await get_positions(connection)

        for position in positions:
            position_side = str(position.get("type", "")).upper()

            if side == "BUY" and position_side in ("BUY", "POSITION_TYPE_BUY"):
                return position

            if side == "SELL" and position_side in ("SELL", "POSITION_TYPE_SELL"):
                return position

        await asyncio.sleep(0.25)

    return None


async def ensure_stops(connection):
    positions = await get_positions(connection)
    if not positions:
        return

    _, digits, _, _ = await get_market(connection)
    epsilon = 10 ** (-digits) / 2

    for position in positions:
        position_id = position.get("id")
        side = str(position.get("type", "")).upper()
        entry = float(position.get("openPrice", 0) or 0)

        if not position_id or entry <= 0:
            continue

        exact_sl, exact_tp = levels(side, entry, digits)

        current_sl_raw = position.get("stopLoss")
        current_tp_raw = position.get("takeProfit")

        current_sl = float(current_sl_raw) if current_sl_raw not in (None, 0) else None
        current_tp = float(current_tp_raw) if current_tp_raw not in (None, 0) else None

        if side in ("BUY", "POSITION_TYPE_BUY"):
            target_sl = current_sl if (current_sl is not None and current_sl >= entry) else exact_sl
        elif side in ("SELL", "POSITION_TYPE_SELL"):
            target_sl = current_sl if (current_sl is not None and current_sl <= entry) else exact_sl
        else:
            continue

        needs_sl = current_sl is None or abs(current_sl - target_sl) > epsilon
        needs_tp = current_tp is None or abs(current_tp - exact_tp) > epsilon

        if needs_sl or needs_tp:
            await asyncio.wait_for(
                connection.modify_position(position_id, target_sl, exact_tp),
                timeout=META_TIMEOUT,
            )


# =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(connection, side):
    _, digits, bid, ask = await get_market(connection)

    provisional_entry = ask if side == "BUY" else bid
    provisional_sl, provisional_tp = levels(side, provisional_entry, digits)

    if side == "BUY":
        result = await asyncio.wait_for(
            connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                provisional_sl,
                provisional_tp,
                {"comment": COMMENT},
            ),
            timeout=META_TIMEOUT,
        )
    else:
        result = await asyncio.wait_for(
            connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                provisional_sl,
                provisional_tp,
                {"comment": COMMENT},
            ),
            timeout=META_TIMEOUT,
        )

    position = await wait_position(connection, side)

    if position:
        position_id = position.get("id")
        actual_entry = float(position.get("openPrice", 0) or 0)
        exact_sl, exact_tp = levels(side, actual_entry, digits)

        await asyncio.wait_for(
            connection.modify_position(position_id, exact_sl, exact_tp),
            timeout=META_TIMEOUT,
        )

        telegram(
            "RIObot GOLD\n\n"
            f"{side} {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            f"Entry: {actual_entry:.2f}\n"
            f"SL: {exact_sl:.2f}\n"
            f"TP: {exact_tp:.2f}\n"
            "M1 PSAR: 1st DOT + 10s\n"
            "M5: 2 PSAR dots same direction\n"
            "ENTRY: breakout -> retest -> confirm\n"
            "NO CHASE / NO BIG IMPULSE\n"
            "SETUP EXPIRES: 4 min\n"
            "BE1: +4.00 -> +1.00\n"
            "BE2: +7.00 -> +5.00\n"
            "NEWS: HIGH USD -15m / +30m"
        )

    return result


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):
    positions = await get_positions(connection)
    if not positions:
        return

    _, digits, bid, ask = await get_market(connection)

    for position in positions:
        position_id = position.get("id")
        side = str(position.get("type", "")).upper()
        entry = float(position.get("openPrice", 0) or 0)

        if not position_id or entry <= 0:
            continue

        current_sl_raw = position.get("stopLoss")
        current_tp_raw = position.get("takeProfit")

        current_sl = float(current_sl_raw) if current_sl_raw not in (None, 0) else None
        current_tp = float(current_tp_raw) if current_tp_raw not in (None, 0) else None

        if side in ("BUY", "POSITION_TYPE_BUY"):
            profit = bid - entry

            if profit >= BE2_TRIGGER:
                new_sl = round(entry + BE2_LOCK, digits)
                label = "BE2"
                reached = "+7.00"
                locked = "+5.00"
            elif profit >= BE1_TRIGGER:
                new_sl = round(entry + BE1_LOCK, digits)
                label = "BE1"
                reached = "+4.00"
                locked = "+1.00"
            else:
                continue

            improve = current_sl is None or current_sl < new_sl

        elif side in ("SELL", "POSITION_TYPE_SELL"):
            profit = entry - ask

            if profit >= BE2_TRIGGER:
                new_sl = round(entry - BE2_LOCK, digits)
                label = "BE2"
                reached = "+7.00"
                locked = "+5.00"
            elif profit >= BE1_TRIGGER:
                new_sl = round(entry - BE1_LOCK, digits)
                label = "BE1"
                reached = "+4.00"
                locked = "+1.00"
            else:
                continue

            improve = current_sl is None or current_sl > new_sl
        else:
            continue

        if improve:
            await asyncio.wait_for(
                connection.modify_position(position_id, new_sl, current_tp),
                timeout=META_TIMEOUT,
            )

            clean_side = side.replace("POSITION_TYPE_", "")
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
            str(value).strip().replace("Z", "+00:00")
        ).timestamp()
    except Exception:
        return None


async def refresh_news_calendar(state, force=False):
    now = time.time()

    if not force and now < state.get("news_next_fetch", 0):
        return

    state["news_next_fetch"] = now + NEWS_FETCH_SECONDS

    def fetch():
        response = requests.get(
            NEWS_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "RIObot-GOLD/1.0"
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    try:
        raw = await asyncio.to_thread(fetch)
        events = []

        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue

                country = str(item.get("country", "")).upper().strip()
                impact = str(item.get("impact", "")).upper().strip()

                if country != "USD" or impact != "HIGH":
                    continue

                timestamp = parse_news_datetime(item.get("date"))
                if timestamp is None:
                    continue

                events.append(
                    {
                        "ts": timestamp,
                        "title": str(item.get("title", "HIGH USD")).strip() or "HIGH USD",
                    }
                )

        events.sort(key=lambda x: x["ts"])
        state["news_events"] = events
        state["news_last_success"] = now
        state["news_error_notified"] = False

        print(f"NEWS CALENDAR OK: {len(events)} HIGH USD events", flush=True)

    except Exception as exc:
        print(f"NEWS WARNING: {type(exc).__name__}: {exc}", flush=True)

        if not state.get("news_error_notified"):
            telegram(
                "RIObot GOLD NEWS WARNING\n\n"
                "Calendar unavailable.\n"
                "New entries are blocked."
            )
            state["news_error_notified"] = True


def news_block_status(state):
    now = time.time()
    last_ok = float(state.get("news_last_success", 0) or 0)

    if last_ok <= 0 or now - last_ok > NEWS_STALE_SECONDS:
        return True, "calendar unavailable/stale", None

    before = NEWS_BEFORE_MINUTES * 60
    after = NEWS_AFTER_MINUTES * 60

    for event in state.get("news_events", []):
        if event["ts"] - before <= now <= event["ts"] + after:
            return True, "HIGH USD", event

    return False, None, None


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


def arm_setup(state, signal_key, side, level, current_price, params):
    state["armed"] = True
    state["setup_phase"] = "WAIT_BREAK"
    state["setup_side"] = side
    state["setup_key"] = signal_key
    state["setup_level"] = level
    state["setup_started"] = time.time()
    state["setup_break_time"] = 0.0
    state["setup_break_price"] = None
    state["setup_last_price"] = current_price
    state["setup_params"] = params
    state["retest_seen"] = False


def fresh_cross(side, previous_price, current_price, level):
    if side == "BUY":
        return previous_price <= level and current_price > level
    return previous_price >= level and current_price < level


# =========================================================
# SESSION
# =========================================================

async def bot_session(state):
    api = MetaApi(M_TOKEN)
    connection = None

    try:
        account = await asyncio.wait_for(
            api.metatrader_account_api.get_account(M_ACC),
            timeout=META_TIMEOUT,
        )

        if getattr(account, "state", None) != "DEPLOYED":
            await asyncio.wait_for(account.deploy(), timeout=180)

        if getattr(account, "connection_status", None) != "CONNECTED":
            await asyncio.wait_for(account.wait_connected(), timeout=180)

        region = getattr(account, "region", None) or DEFAULT_META_REGION
        state["region"] = str(region).lower()

        connection = account.get_rpc_connection()
        await asyncio.wait_for(connection.connect(), timeout=60)
        await asyncio.wait_for(connection.wait_synchronized(), timeout=120)

        print("RIObot GOLD CONNECTED", flush=True)

        if not state["ever_connected"]:
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
            state["ever_connected"] = True
        else:
            telegram("RIObot GOLD RECONNECTED\n\nMetaApi connection restored.")

        await seed_history(state)
        await refresh_news_calendar(state, force=True)

        loop_errors = 0

        while True:
            try:
                await ensure_stops(connection)
                await manage_be(connection)
                await refresh_news_calendar(state)

                candle = await get_current_m1(state["region"])
                if candle is None:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                update_cache(state["m1_candles"], candle)

                df1 = make_m1_df(state["m1_candles"])
                df5 = make_m5_df(state["m1_candles"])

                if df1 is None or df5 is None:
                    await seed_history(state)
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                signal = get_live_flip(df1)
                candle_time = str(df1.iloc[-1]["time"])

                positions = await get_positions(connection)
                had_position = state.get("had_position", False)

                if positions:
                    state["had_position"] = True
                    clear_pending(state)
                    clear_setup(state)
                    loop_errors = 0
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                if had_position and not positions:
                    state["had_position"] = False
                    state["require_new_flip"] = True
                    clear_pending(state)
                    clear_setup(state)
                    print("POSITION CLOSED -> NEW PSAR FLIP REQUIRED", flush=True)

                news_blocked, news_reason, news_event = news_block_status(state)
                if news_blocked:
                    if state.get("armed"):
                        clear_setup(state)

                    event_key = (
                        f"{news_reason}:{news_event['ts']}"
                        if news_event else news_reason
                    )

                    if state.get("news_block_notified") != event_key:
                        if news_event:
                            event_time = datetime.fromtimestamp(
                                news_event["ts"]
                            ).astimezone().strftime("%H:%M")
                            telegram(
                                "RIObot GOLD NEWS BLOCK\n\n"
                                f"HIGH USD: {news_event['title']}\n"
                                f"Time: {event_time}\n"
                                "No new trade -15m / +30m."
                            )
                        else:
                            telegram(
                                "RIObot GOLD NEWS BLOCK\n\n"
                                "Calendar unavailable/stale.\n"
                                "No new trades."
                            )
                        state["news_block_notified"] = event_key

                    loop_errors = 0
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                state["news_block_notified"] = None

                if signal:
                    signal_key = f"{candle_time}:{signal}"

                    if (
                        signal_key != state.get("last_signal_key")
                        and signal_key != state.get("setup_key")
                    ):
                        if state.get("pending_signal_key") != signal_key:
                            clear_setup(state)
                            state["pending_signal_key"] = signal_key
                            state["pending_signal_side"] = signal
                            state["pending_started"] = time.monotonic()
                            print(f"1ST DOT PENDING 10S {signal}", flush=True)
                        else:
                            elapsed = time.monotonic() - float(state.get("pending_started", 0) or 0)
                            if elapsed >= SIGNAL_CONFIRM_SECONDS and signal == state.get("pending_signal_side"):
                                if m5_confirms(df5, signal):
                                    last_closed = df1.iloc[-2]
                                    breakout_level = (
                                        float(last_closed["high"])
                                        if signal == "BUY"
                                        else float(last_closed["low"])
                                    )
                                    _, _, bid, ask = await get_market(connection)
                                    current_price = bid if signal == "BUY" else ask
                                    params = params_for_setup(df1)

                                    arm_setup(
                                        state,
                                        signal_key,
                                        signal,
                                        breakout_level,
                                        current_price,
                                        params,
                                    )
                                    clear_pending(state)
                                    state["require_new_flip"] = False
                                    print(
                                        f"SETUP ARMED {signal} LEVEL={breakout_level:.2f} PHASE=WAIT_BREAK",
                                        flush=True,
                                    )
                                else:
                                    clear_pending(state)
                                    print(f"M5 NOT CONFIRMED {signal}", flush=True)
                elif state.get("pending_signal_key"):
                    clear_pending(state)

                if state.get("armed"):
                    side = state["setup_side"]
                    phase = state["setup_phase"]
                    level = float(state["setup_level"])
                    params = state["setup_params"] or {}
                    started = float(state["setup_started"] or 0)
                    now = time.time()

                    if now - started > SETUP_EXPIRY_SECONDS:
                        print("SETUP EXPIRED", flush=True)
                        clear_setup(state)
                    elif current_psar_side(df1) != side or not m5_confirms(df5, side):
                        print("SETUP CANCELLED: PSAR/M5 changed", flush=True)
                        clear_setup(state)
                    elif big_impulse(df1):
                        print("SETUP CANCELLED: BIG IMPULSE", flush=True)
                        clear_setup(state)
                    else:
                        _, _, bid, ask = await get_market(connection)
                        current_price = bid if side == "BUY" else ask

                        previous_price = state.get("setup_last_price")
                        if previous_price is None:
                            previous_price = current_price

                        state["setup_last_price"] = current_price

                        max_chase = float(params.get("max_chase", 1.0))
                        retest_band = float(params.get("retest", 0.25))
                        invalidate = float(params.get("invalidate", 0.5))
                        confirm_dist = float(params.get("confirm", 0.15))
                        extension = float(params.get("extension", 0.35))

                        if phase == "WAIT_BREAK":
                            too_far = (
                                current_price > level + max_chase
                                if side == "BUY"
                                else current_price < level - max_chase
                            )

                            if too_far:
                                print("SETUP CANCELLED: CHASE TOO FAR", flush=True)
                                clear_setup(state)
                            elif fresh_cross(side, previous_price, current_price, level):
                                state["setup_phase"] = "WAIT_RETEST"
                                state["setup_break_time"] = now
                                state["setup_break_price"] = current_price
                                print(
                                    f"BREAKOUT {side} LEVEL={level:.2f} PRICE={current_price:.2f}",
                                    flush=True,
                                )

                        elif phase == "WAIT_RETEST":
                            break_time = float(state.get("setup_break_time", 0) or 0)

                            if break_time > 0 and now - break_time > RETEST_EXPIRY_SECONDS:
                                print("SETUP CANCELLED: RETEST TIMEOUT", flush=True)
                                clear_setup(state)
                                continue

                            invalid_now = (
                                current_price < level - invalidate
                                if side == "BUY"
                                else current_price > level + invalidate
                            )
                            if invalid_now:
                                print("SETUP CANCELLED: RETEST INVALIDATION", flush=True)
                                clear_setup(state)
                                continue

                            too_extended = (
                                current_price > level + extension and not state.get("retest_seen")
                                if side == "BUY"
                                else current_price < level - extension and not state.get("retest_seen")
                            )
                            if too_extended:
                                print("SETUP CANCELLED: NO RETEST / PRICE RAN", flush=True)
                                clear_setup(state)
                                continue

                            touched = (
                                current_price <= level + retest_band
                                if side == "BUY"
                                else current_price >= level - retest_band
                            )
                            if touched:
                                state["retest_seen"] = True
                                state["setup_phase"] = "WAIT_CONFIRM"
                                print(f"RETEST OK {side} LEVEL={level:.2f}", flush=True)

                        elif phase == "WAIT_CONFIRM":
                            invalid_now = (
                                current_price < level - invalidate
                                if side == "BUY"
                                else current_price > level + invalidate
                            )
                            if invalid_now:
                                print("SETUP CANCELLED: CONFIRM INVALIDATION", flush=True)
                                clear_setup(state)
                                continue

                            confirmed = (
                                current_price >= level + confirm_dist
                                if side == "BUY"
                                else current_price <= level - confirm_dist
                            )
                            if confirmed:
                                blocked_again, _, _ = news_block_status(state)
                                positions_again = await get_positions(connection)

                                if not blocked_again and not positions_again:
                                    used_key = state.get("setup_key")
                                    print(
                                        f"ENTRY CONFIRMED {side} LEVEL={level:.2f} PRICE={current_price:.2f}",
                                        flush=True,
                                    )
                                    clear_setup(state)
                                    await open_trade(connection, side)
                                    state["last_signal_key"] = used_key
                                    state["require_new_flip"] = True

                loop_errors = 0

            except Exception as exc:
                loop_errors += 1
                print(
                    f"LOOP WARNING {loop_errors}/{MAX_LOOP_ERRORS}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

                if loop_errors >= MAX_LOOP_ERRORS:
                    raise

                await asyncio.sleep(RECONNECT_SECONDS)

            await asyncio.sleep(LOOP_SECONDS)

    finally:
        if connection is not None:
            try:
                await asyncio.wait_for(connection.close(), timeout=10)
            except Exception:
                pass


# =========================================================
# MAIN
# =========================================================

async def main():
    keep_alive()

    if not M_TOKEN:
        raise RuntimeError("M_TOKEN is missing")

    if not M_ACC:
        raise RuntimeError("M_ACC is missing")

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
            print(f"BOT SESSION ERROR: {type(exc).__name__}: {exc}", flush=True)
            telegram(
                "RIObot GOLD CONNECTION ERROR\n\n"
                f"Reconnect in {RECONNECT_SECONDS} seconds."
            )
            await asyncio.sleep(RECONNECT_SECONDS)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
