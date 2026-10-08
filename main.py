import os
import json
import time
import asyncio
import traceback
import requests

from datetime import datetime, timezone, timedelta
from threading import Thread
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =====================================================
# RIOBOT GOLD V14.17 - FAST SCALP
# M5 DIRECTION + M1 MOMENTUM / RETEST
# SHARED BE / 20 POSITIONS
# =====================================================

VERSION = "V14.17"
SYMBOL = "XAUUSD"
COMMENT_PREFIX = "RIOGOLDV14"

LOT_SIZE = 0.10
BATCH_SIZE = 20
MAX_TRADES = 20

TP_GROUPS = [
    ("TP1", 10, 0.60),
    ("TP2", 5, 0.90),
    ("TP3", 5, 1.20),
]

ENABLE_TRADING = True

LOOP_SECONDS = 2
PROTECTION_LOOP_SECONDS = 1
COOLDOWN_SECONDS = 180

RECONNECT_SECONDS = 3
RPC_TIMEOUT = 25
RPC_RETRIES = 3
ORDER_TIMEOUT = 45

M1_HISTORY_LIMIT = 1000
MIN_M1_HISTORY = 800
MAX_ENTRY_CANDLE_AGE = 130
RECENT_CONTINUITY_BARS = 45

ATR_PERIOD = 14

MAX_SPREAD = 0.50
MAX_FORWARD_DRIFT_ATR = 0.40
MAX_ADVERSE_DRIFT_ATR = 0.18

M1_MAX_EMA9_DISTANCE_ATR = 0.85
LIVE_EMA9_TOLERANCE_ATR = 0.08

M1_RETEST_LOOKBACK = 5
M1_RETEST_TOLERANCE_ATR = 0.45
M1_MIN_BODY_RATIO = 0.25
M1_MIN_WICK_RATIO = 0.12
MAX_M1_RANGE_ATR = 2.20

FAST_MIN_BODY_RATIO = 0.30
FAST_BREAK_BUFFER_ATR = 0.03
FAST_CLOSE_POSITION_MIN = 0.60

# Faster M5 direction, but still requires EMA trend.
M5_SLOPE_BARS = 2
M5_MIN_SLOPE_ATR = 0.02

SL_SWING_LOOKBACK = 16
SL_FALLBACK_BARS = 10
SL_SWING_BUFFER_ATR = 0.75

MIN_SL_DISTANCE = 1.80
MAX_SL_DISTANCE = 4.50
MIN_SL_ATR_MULT = 1.40
MAX_SL_ATR_MULT = 3.50

BE1_TRIGGER_RR = 0.35
BE1_LOCK_DISTANCE = 0.15

BE2_TRIGGER_RR = 0.40
BE2_LOCK_RR = 0.22

BE3_TRIGGER_RR = 0.60
BE3_LOCK_RR = 0.38

MIN_BE_PROFIT_DISTANCE = 0.15
NEW_SETUP_RELEASE_ATR = 0.50

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

STATE_DIR = os.getenv("RIO_STATE_DIR", "/tmp")
STATE_FILE = os.path.join(
    STATE_DIR, "rio_gold_v14.json"
)

app = Flask(__name__)


@app.route("/")
def home():
    return f"RIOBOT GOLD {VERSION} ACTIVE", 200


def keep_alive():

    def run():
        app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", "10000")),
            use_reloader=False
        )

    Thread(target=run, daemon=True).start()


def telegram(message):

    print(message, flush=True)

    if not T_TOKEN or not T_CHAT:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=6
        )
    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


def notify(message):
    asyncio.create_task(
        asyncio.to_thread(telegram, message)
    )


def default_state():
    return {
        "positions": {},
        "last_trade_time": 0,
        "last_candle": None,
        "last_signal": None,
        "locked_side": None,
        "locked_reference": None,
        "setup_released": True,
        "batch_be_stage": 0,
        "order_uncertain": False,
        "halted": False
    }


def save_state(state):

    os.makedirs(STATE_DIR, exist_ok=True)
    temp = STATE_FILE + ".tmp"

    with open(temp, "w") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp, STATE_FILE)


def load_state():

    state = default_state()

    if not os.path.exists(STATE_FILE):
        return state

    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)

        for key in state:
            if key in saved:
                state[key] = saved[key]

    except Exception:
        print(
            "STATE ERROR - TRADING HALTED",
            traceback.format_exc(),
            flush=True
        )
        state["halted"] = True
        state["order_uncertain"] = True

    return state


async def meta_call(
    factory,
    timeout=RPC_TIMEOUT,
    retries=RPC_RETRIES
):

    last_error = None

    for attempt in range(retries):

        try:
            return await asyncio.wait_for(
                factory(), timeout=timeout
            )

        except Exception as e:
            last_error = e

            temporary = isinstance(
                e, (asyncio.TimeoutError, TimeoutError)
            ) or any(
                word in str(e).lower()
                for word in (
                    "timeout",
                    "not connected",
                    "not synchronized",
                    "websocket",
                    "subscription",
                    "disconnected",
                    "connection lost",
                    "broker yet"
                )
            )

            if not temporary:
                raise

            if attempt < retries - 1:
                await asyncio.sleep(2)

    raise RuntimeError(
        f"METAAPI TEMPORARY ERROR: {last_error}"
    )


def parse_time(value):

    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


async def get_candles(region):

    now = datetime.now(timezone.utc)

    url = (
        f"https://mt-market-data-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/"
        f"historical-market-data/symbols/"
        f"{SYMBOL}/timeframes/1m/candles"
    )

    def fetch():

        response = requests.get(
            url,
            headers={"auth-token": M_TOKEN},
            params={
                "startTime": iso_z(
                    now + timedelta(minutes=1)
                ),
                "limit": M1_HISTORY_LIMIT
            },
            timeout=20
        )

        response.raise_for_status()
        return response.json()

    raw = await asyncio.to_thread(fetch)

    if not isinstance(raw, list):
        raise RuntimeError("INVALID M1 HISTORY")

    candles_by_time = {}

    for item in raw:

        try:
            dt = parse_time(item["time"])

            if (now - dt).total_seconds() < 61:
                continue

            candles_by_time[dt.isoformat()] = {
                "time": dt.isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            }

        except (KeyError, ValueError, TypeError):
            continue

    candles = [
        candles_by_time[key]
        for key in sorted(candles_by_time)
    ]

    if len(candles) < MIN_M1_HISTORY:
        print(
            "WAIT M1 HISTORY:",
            len(candles),
            flush=True
        )
        return None

    age = (
        now - parse_time(candles[-1]["time"])
    ).total_seconds()

    if age > MAX_ENTRY_CANDLE_AGE:
        print(
            f"WAIT STALE M1 AGE={age:.0f}s",
            flush=True
        )
        return None

    recent = candles[-RECENT_CONTINUITY_BARS:]

    for previous, current in zip(
        recent[:-1], recent[1:]
    ):
        gap = (
            parse_time(current["time"])
            - parse_time(previous["time"])
        ).total_seconds()

        if gap != 60:
            print(
                "WAIT RECENT M1 HISTORY GAP",
                gap,
                flush=True
            )
            return None

    return candles


def calculate_atr(candles):

    if len(candles) < ATR_PERIOD + 2:
        return None

    ranges = []

    for i in range(1, len(candles)):

        c = candles[i]
        p = candles[i - 1]

        ranges.append(max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"])
        ))

    return sum(ranges[-ATR_PERIOD:]) / ATR_PERIOD


def ema_series(values, period):

    if len(values) < period:
        return []

    value = sum(values[:period]) / period
    result = [value]

    multiplier = 2 / (period + 1)

    for price in values[period:]:
        value = (
            price * multiplier
            + value * (1 - multiplier)
        )
        result.append(value)

    return result


def build_tf(candles, minutes):

    groups = {}

    for candle in candles:

        dt = parse_time(candle["time"])

        key = dt.replace(
            minute=dt.minute - dt.minute % minutes,
            second=0,
            microsecond=0
        ).isoformat()

        if key not in groups:

            groups[key] = {
                "time": key,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "count": 1
            }

        else:

            g = groups[key]
            g["high"] = max(g["high"], candle["high"])
            g["low"] = min(g["low"], candle["low"])
            g["close"] = candle["close"]
            g["count"] += 1

    return sorted(
        [
            c for c in groups.values()
            if c["count"] == minutes
        ],
        key=lambda c: c["time"]
    )


# =====================================================
# M15 - INFORMATION ONLY
# =====================================================

def m15_direction(candles):

    bars = build_tf(candles, 15)

    if len(bars) < 53:
        return None

    closes = [c["close"] for c in bars]

    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)

    if (
        closes[-1] > e20[-1] > e50[-1]
        and e20[-1] > e20[-2]
    ):
        return "BUY"

    if (
        closes[-1] < e20[-1] < e50[-1]
        and e20[-1] < e20[-2]
    ):
        return "SELL"

    return None


# =====================================================
# V14.17 - FASTER M5 TREND
# No mandatory same-direction candle.
# =====================================================

def m5_direction(candles):

    bars = build_tf(candles, 5)

    if len(bars) < 23:
        return None

    closes = [c["close"] for c in bars]

    e9 = ema_series(closes, 9)
    e20 = ema_series(closes, 20)

    current = bars[-1]

    # Require recent M5 candles to be continuous.
    if (
        parse_time(bars[-1]["time"])
        - parse_time(bars[-2]["time"])
    ).total_seconds() != 300:
        return None

    slope = e9[-1] - e9[-1 - M5_SLOPE_BARS]

    recent_ranges = [
        b["high"] - b["low"]
        for b in bars[-14:]
    ]

    average_range = (
        sum(recent_ranges) / len(recent_ranges)
    )

    minimum_slope = (
        average_range * M5_MIN_SLOPE_ATR
    )

    if (
        current["close"] > e9[-1] > e20[-1]
        and slope > minimum_slope
    ):
        return "BUY"

    if (
        current["close"] < e9[-1] < e20[-1]
        and slope < -minimum_slope
    ):
        return "SELL"

    return None


# =====================================================
# V14.17 - M1 FAST SCALP / RETEST
# =====================================================

def m1_trigger(candles, side, atr):

    if len(candles) < 30:
        return None

    current = candles[-1]
    previous = candles[-2]

    rng = current["high"] - current["low"]

    if rng <= 0 or rng > MAX_M1_RANGE_ATR * atr:
        return None

    closes = [c["close"] for c in candles]

    e9_values = ema_series(closes, 9)
    e20_values = ema_series(closes, 20)

    e9 = e9_values[-1]
    e20 = e20_values[-1]

    body = abs(
        current["close"] - current["open"]
    ) / rng

    if side == "BUY":

        aligned = (
            current["close"] > e9 > e20
            and e9 > e9_values[-2]
        )

        direction_ok = (
            current["close"] > current["open"]
        )

    else:

        aligned = (
            current["close"] < e9 < e20
            and e9 < e9_values[-2]
        )

        direction_ok = (
            current["close"] < current["open"]
        )

    if not aligned or not direction_ok:
        return None

    if abs(current["close"] - e9) > (
        M1_MAX_EMA9_DISTANCE_ATR * atr
    ):
        return None

    close_position = (
        current["close"] - current["low"]
    ) / rng

    if side == "BUY":

        breakout = (
            current["close"]
            > previous["high"] + FAST_BREAK_BUFFER_ATR * atr
        )

        strong_close = (
            close_position >= FAST_CLOSE_POSITION_MIN
        )

    else:

        breakout = (
            current["close"]
            < previous["low"] - FAST_BREAK_BUFFER_ATR * atr
        )

        strong_close = (
            close_position <= (
                1 - FAST_CLOSE_POSITION_MIN
            )
        )

    if (
        body >= FAST_MIN_BODY_RATIO
        and breakout
        and strong_close
    ):
        return {
            "mode": "FAST SCALP",
            "close": current["close"],
            "ema9": e9
        }

    tolerance = atr * M1_RETEST_TOLERANCE_ATR

    recent = candles[
        -(M1_RETEST_LOOKBACK + 1):-1
    ]

    retest_found = False

    for candle in recent:

        if side == "BUY":
            if candle["low"] <= e9 + tolerance:
                retest_found = True
        else:
            if candle["high"] >= e9 - tolerance:
                retest_found = True

    if side == "BUY":

        retest_found = (
            retest_found
            or current["low"] <= e9 + tolerance
        )

        momentum = (
            current["close"] > previous["close"]
            and current["high"] > previous["high"]
        )

        lower_wick = (
            min(current["open"], current["close"])
            - current["low"]
        ) / rng

        wick_confirm = (
            lower_wick >= M1_MIN_WICK_RATIO
            and current["close"] > previous["close"]
        )

    else:

        retest_found = (
            retest_found
            or current["high"] >= e9 - tolerance
        )

        momentum = (
            current["close"] < previous["close"]
            and current["low"] < previous["low"]
        )

        upper_wick = (
            current["high"]
            - max(current["open"], current["close"])
        ) / rng

        wick_confirm = (
            upper_wick >= M1_MIN_WICK_RATIO
            and current["close"] < previous["close"]
        )

    if (
        body >= M1_MIN_BODY_RATIO
        and retest_found
        and (momentum or wick_confirm)
    ):
        return {
            "mode": "FAST RETEST",
            "close": current["close"],
            "ema9": e9
        }

    return None


def protective_swing(candles, side):

    n = len(candles)
    candidates = []

    for i in range(
        max(2, n - SL_SWING_LOOKBACK),
        n - 2
    ):

        if side == "BUY":

            if all(
                candles[i]["low"] <= candles[j]["low"]
                for j in (i-2, i-1, i+1, i+2)
            ):
                candidates.append(candles[i]["low"])

        else:

            if all(
                candles[i]["high"] >= candles[j]["high"]
                for j in (i-2, i-1, i+1, i+2)
            ):
                candidates.append(candles[i]["high"])

    if candidates:
        return candidates[-1]

    recent = candles[-(SL_FALLBACK_BARS+1):-1]

    if side == "BUY":
        return min(c["low"] for c in recent)

    return max(c["high"] for c in recent)


def position_side(position):

    value = str(position.get("type", "")).upper()

    if value in (
        "POSITION_TYPE_BUY", "ORDER_TYPE_BUY", "BUY", "0"
    ):
        return "BUY"

    if value in (
        "POSITION_TYPE_SELL", "ORDER_TYPE_SELL", "SELL", "1"
    ):
        return "SELL"

    return None


def is_managed(position):

    return str(
        position.get("comment") or ""
    ).upper().startswith(COMMENT_PREFIX)


async def get_positions(connection):

    result = await meta_call(
        lambda: connection.get_positions()
    )

    if not isinstance(result, list):
        raise RuntimeError("INVALID POSITIONS RESPONSE")

    return [
        p for p in result
        if str(p.get("symbol", "")).upper() == SYMBOL
    ]


async def get_market(connection):

    spec = await meta_call(
        lambda: connection.get_symbol_specification(SYMBOL)
    )

    price = await meta_call(
        lambda: connection.get_symbol_price(SYMBOL)
    )

    digits = int(spec.get("digits", 2))

    tick = float(
        spec.get("tickSize") or 10 ** (-digits)
    )

    point = float(
        spec.get("point") or 10 ** (-digits)
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    if tick <= 0 or bid <= 0 or ask <= bid:
        raise RuntimeError("INVALID MARKET QUOTE")

    stop_level = float(
        spec.get("stopsLevel") or 0
    )

    freeze_level = float(
        spec.get("freezeLevel") or 0
    )

    minimum = max(
        stop_level,
        freeze_level
    ) * point + tick * 2

    return {
        "spec": spec,
        "bid": bid,
        "ask": ask,
        "tick": tick,
        "digits": digits,
        "min_distance": max(minimum, tick * 2)
    }


def normalize(value, market):

    tick = market["tick"]

    return round(
        round(value / tick) * tick,
        market["digits"]
    )


def validate_market_levels(side, sl, tp, market):

    minimum = market["min_distance"]

    if side == "BUY":
        return (
            sl < market["bid"] - minimum
            and tp > market["ask"] + minimum
        )

    return (
        sl > market["ask"] + minimum
        and tp < market["bid"] - minimum
    )


def get_levels(side, entry, anchor, atr, market):

    minimum_risk = max(
        MIN_SL_DISTANCE,
        MIN_SL_ATR_MULT * atr,
        market["min_distance"] + market["tick"]
    )

    maximum_risk = min(
        MAX_SL_DISTANCE,
        MAX_SL_ATR_MULT * atr
    )

    if side == "BUY":

        desired_sl = (
            anchor - atr * SL_SWING_BUFFER_ATR
        )

        structural_risk = entry - desired_sl

    else:

        desired_sl = (
            anchor + atr * SL_SWING_BUFFER_ATR
        )

        structural_risk = desired_sl - entry

    risk = max(
        structural_risk,
        minimum_risk
    )

    if risk <= 0 or risk > maximum_risk:
        return None

    direction = 1 if side == "BUY" else -1

    sl = normalize(
        entry - direction * risk,
        market
    )

    tps = {}

    for name, count, rr in TP_GROUPS:

        tps[name] = normalize(
            entry + direction * risk * rr,
            market
        )

        if not validate_market_levels(
            side, sl, tps[name], market
        ):
            return None

    return {
        "sl": sl,
        "tps": tps,
        "risk": abs(entry - sl)
    }


def group_from_comment(comment):

    value = str(comment or "").upper()

    for name, count, rr in TP_GROUPS:
        if name in value:
            return name, rr

    return None, None


def adopt_positions(state, positions):

    adopted = 0

    for position in positions:

        pid = str(position["id"])

        if pid in state["positions"]:
            continue

        side = position_side(position)

        entry = float(
            position.get("openPrice") or 0
        )

        sl = float(
            position.get("stopLoss") or 0
        )

        tp = float(
            position.get("takeProfit") or 0
        )

        group, rr = group_from_comment(
            position.get("comment")
        )

        if not side or entry <= 0:
            continue

        if tp > 0 and rr:
            risk = abs(tp - entry) / rr
        elif sl > 0:
            risk = abs(entry - sl)
        else:
            continue

        if risk <= 0:
            continue

        state["positions"][pid] = {
            "entry": entry,
            "risk": risk,
            "side": side,
            "tp_group": group
        }

        adopted += 1

    if adopted:
        save_state(state)
        notify(
            f"RIO GOLD {VERSION} ADOPTED: {adopted}"
        )


def reconcile_state(state, all_positions):

    managed = [
        p for p in all_positions
        if is_managed(p)
    ]

    active_ids = {
        str(p["id"]) for p in managed
    }

    known_ids = set(state["positions"])

    for pid in known_ids - active_ids:

        state["positions"].pop(pid, None)
        state["last_trade_time"] = time.time()

    adopt_positions(state, managed)

    if not managed and not state["positions"]:
        state["batch_be_stage"] = 0

    save_state(state)

    return managed


def update_setup_lock(state, candles, atr):

    side = state["locked_side"]
    reference = state["locked_reference"]

    if (
        not side
        or reference is None
        or state["setup_released"]
    ):
        return

    closes = [c["close"] for c in candles]
    ema20 = ema_series(closes, 20)[-1]
    last_close = closes[-1]

    if side == "BUY":

        reset = (
            last_close <= ema20
            or last_close <= (
                reference - atr * NEW_SETUP_RELEASE_ATR
            )
        )

    else:

        reset = (
            last_close >= ema20
            or last_close >= (
                reference + atr * NEW_SETUP_RELEASE_ATR
            )
        )

    if reset:

        state["locked_side"] = None
        state["locked_reference"] = None
        state["setup_released"] = True
        state["last_signal"] = None

        save_state(state)


async def protect_positions(
    connection, managed, state, market
):

    if not managed:
        return

    measured = []

    for position in managed:

        pid = str(position["id"])
        info = state["positions"].get(pid)

        if not info:
            print(
                "BE WAIT: UNKNOWN POSITION",
                pid,
                flush=True
            )
            return

        side = position_side(position)

        entry = float(
            position.get("openPrice") or 0
        )

        risk = float(info.get("risk") or 0)

        if not side or entry <= 0 or risk <= 0:
            return

        if side == "BUY":
            profit = market["bid"] - entry
        else:
            profit = entry - market["ask"]

        measured.append(profit / risk)

    weakest_rr = min(measured)

    current_stage = 0

    if weakest_rr >= BE3_TRIGGER_RR:
        current_stage = 3
    elif weakest_rr >= BE2_TRIGGER_RR:
        current_stage = 2
    elif weakest_rr >= BE1_TRIGGER_RR:
        current_stage = 1

    previous_stage = int(
        state.get("batch_be_stage", 0)
    )

    if current_stage > previous_stage:

        state["batch_be_stage"] = current_stage
        save_state(state)

        notify(
            f"RIO GOLD {VERSION} SHARED BE"
            f"{current_stage} TRIGGERED\n"
            f"WEAKEST: {weakest_rr:.2f}R"
        )

    stage = int(state["batch_be_stage"])

    if stage <= 0:
        return

    for position in managed:

        pid = str(position["id"])
        info = state["positions"][pid]

        side = position_side(position)

        entry = float(position["openPrice"])
        risk = float(info["risk"])

        current_sl = float(
            position.get("stopLoss") or 0
        )

        current_tp = position.get("takeProfit")

        if current_sl <= 0:
            notify(
                f"RIO GOLD MISSING SL POSITION {pid}"
            )
            continue

        if stage == 1:
            lock_distance = BE1_LOCK_DISTANCE
        elif stage == 2:
            lock_distance = max(
                BE2_LOCK_RR * risk,
                MIN_BE_PROFIT_DISTANCE
            )
        else:
            lock_distance = max(
                BE3_LOCK_RR * risk,
                MIN_BE_PROFIT_DISTANCE
            )

        if side == "BUY":

            wanted = normalize(
                entry + lock_distance,
                market
            )

            if wanted <= current_sl + market["tick"]/2:
                continue

            if wanted >= (
                market["bid"] - market["min_distance"]
            ):
                continue

        else:

            wanted = normalize(
                entry - lock_distance,
                market
            )

            if wanted >= current_sl - market["tick"]/2:
                continue

            if wanted <= (
                market["ask"] + market["min_distance"]
            ):
                continue

        try:

            await meta_call(
                lambda pid=pid, wanted=wanted,
                       current_tp=current_tp:
                connection.modify_position(
                    pid, wanted, current_tp
                ),
                retries=2
            )

            print(
                f"BE{stage} PROTECTED",
                pid,
                wanted,
                flush=True
            )

        except Exception as e:

            notify(
                f"RIO GOLD BE{stage} ERROR\n"
                f"POSITION: {pid}\n"
                f"{str(e)[:150]}"
            )


async def open_batch(
    connection,
    state,
    side,
    signal,
    anchor,
    atr
):

    if state["halted"] or state["order_uncertain"]:
        return

    if (
        state["locked_side"] == side
        and not state["setup_released"]
    ):
        return

    before_positions = await get_positions(connection)

    if before_positions:
        return

    market = await get_market(connection)

    spread = market["ask"] - market["bid"]

    if spread > MAX_SPREAD:
        print("ENTRY BLOCKED: SPREAD", spread)
        return

    reference_entry = (
        market["ask"] if side == "BUY"
        else market["bid"]
    )

    direction = 1 if side == "BUY" else -1

    drift = direction * (
        reference_entry - signal["close"]
    )

    if (
        drift > MAX_FORWARD_DRIFT_ATR * atr
        or drift < -MAX_ADVERSE_DRIFT_ATR * atr
    ):
        print("ENTRY BLOCKED: DRIFT", drift)
        return

    ema_distance = direction * (
        reference_entry - signal["ema9"]
    )

    if (
        ema_distance < -LIVE_EMA9_TOLERANCE_ATR * atr
        or ema_distance > M1_MAX_EMA9_DISTANCE_ATR * atr
    ):
        print("ENTRY BLOCKED: LIVE EMA9")
        return

    if (
        side == "BUY" and anchor >= reference_entry
    ) or (
        side == "SELL" and anchor <= reference_entry
    ):
        print("ENTRY BLOCKED: INVALID SWING")
        return

    levels = get_levels(
        side,
        reference_entry,
        anchor,
        atr,
        market
    )

    if not levels:
        print("ENTRY BLOCKED: SL/TP")
        return

    spec = market["spec"]

    minimum_volume = float(
        spec.get("minVolume") or 0.01
    )

    volume_step = float(
        spec.get("volumeStep") or 0.01
    )

    if (
        LOT_SIZE < minimum_volume
        or volume_step <= 0
        or abs(
            LOT_SIZE / volume_step
            - round(LOT_SIZE / volume_step)
        ) > 1e-7
    ):
        notify("RIO GOLD INVALID LOT SIZE")
        return

    if not ENABLE_TRADING:

        notify(
            f"RIO GOLD {VERSION} TEST SIGNAL\n"
            f"SIDE: {side}\n"
            f"M1: {signal['mode']}\n"
            f"SL: {levels['sl']}\n"
            f"TP: {levels['tps']}"
        )
        return

    previous_ids = {
        str(p["id"]) for p in before_positions
    }

    state["locked_side"] = side
    state["locked_reference"] = signal["close"]
    state["setup_released"] = False
    state["order_uncertain"] = True
    state["batch_be_stage"] = 0

    save_state(state)

    sent = 0

    try:

        for group_name, count, rr in TP_GROUPS:

            for _ in range(count):

                live_market = await get_market(connection)

                live_spread = (
                    live_market["ask"]
                    - live_market["bid"]
                )

                live_entry = (
                    live_market["ask"]
                    if side == "BUY"
                    else live_market["bid"]
                )

                live_drift = direction * (
                    live_entry - signal["close"]
                )

                live_ema_distance = direction * (
                    live_entry - signal["ema9"]
                )

                if (
                    live_spread > MAX_SPREAD
                    or live_drift > MAX_FORWARD_DRIFT_ATR * atr
                    or live_drift < -MAX_ADVERSE_DRIFT_ATR * atr
                    or live_ema_distance
                    < -LIVE_EMA9_TOLERANCE_ATR * atr
                    or live_ema_distance
                    > M1_MAX_EMA9_DISTANCE_ATR * atr
                ):
                    raise RuntimeError(
                        "MARKET MOVED DURING BATCH"
                    )

                sl = levels["sl"]
                tp = levels["tps"][group_name]

                if not validate_market_levels(
                    side, sl, tp, live_market
                ):
                    raise RuntimeError(
                        "SL/TP INVALID DURING BATCH"
                    )

                comment = (
                    f"{COMMENT_PREFIX}_"
                    f"{group_name}_"
                    f"{sent+1:02d}"
                )

                options = {"comment": comment}

                if side == "BUY":

                    result = await meta_call(
                        lambda sl=sl, tp=tp,
                               options=options:
                        connection.create_market_buy_order(
                            SYMBOL,
                            LOT_SIZE,
                            sl,
                            tp,
                            options
                        ),
                        timeout=ORDER_TIMEOUT,
                        retries=1
                    )

                else:

                    result = await meta_call(
                        lambda sl=sl, tp=tp,
                               options=options:
                        connection.create_market_sell_order(
                            SYMBOL,
                            LOT_SIZE,
                            sl,
                            tp,
                            options
                        ),
                        timeout=ORDER_TIMEOUT,
                        retries=1
                    )

                sent += 1

                print(
                    f"ORDER {sent}/{BATCH_SIZE} SENT",
                    result,
                    flush=True
                )

        new_positions = []

        for attempt in range(16):

            current_positions = await get_positions(
                connection
            )

            new_positions = [
                p for p in current_positions
                if (
                    is_managed(p)
                    and str(p["id"]) not in previous_ids
                    and position_side(p) == side
                )
            ]

            if len(new_positions) >= BATCH_SIZE:
                break

            await asyncio.sleep(0.5)

        adopt_positions(state, new_positions)

        if len(new_positions) != BATCH_SIZE:
            raise RuntimeError(
                "BATCH NOT FULLY VERIFIED: "
                f"{len(new_positions)}/{BATCH_SIZE}"
            )

        state["order_uncertain"] = False
        state["halted"] = False
        state["last_trade_time"] = time.time()

        save_state(state)

        notify(
            f"RIO GOLD {VERSION} BATCH VERIFIED\n"
            f"{BATCH_SIZE}/{BATCH_SIZE} POSITIONS\n"
            f"LOT EACH: {LOT_SIZE:.2f}\n"
            f"TOTAL LOT: {BATCH_SIZE * LOT_SIZE:.2f}\n"
            "TP1: 10 x 0.60R\n"
            "TP2: 5 x 0.90R\n"
            "TP3: 5 x 1.20R"
        )

    except Exception as e:

        print(
            "BATCH ERROR:",
            traceback.format_exc(),
            flush=True
        )

        state["halted"] = True
        state["order_uncertain"] = True

        save_state(state)

        notify(
            f"RIO GOLD {VERSION} BATCH HALTED\n"
            f"SENT: {sent}/{BATCH_SIZE}\n"
            f"ERROR: {str(e)[:180]}\n"
            "CHECK OPEN POSITIONS MANUALLY"
        )


async def bot_session(state):

    api = MetaApi(M_TOKEN)

    account = await meta_call(
        lambda:
        api.metatrader_account_api.get_account(M_ACC),
        timeout=40
    )

    region = getattr(account, "region", None) or "london"

    if str(account.state).upper() != "DEPLOYED":

        await meta_call(
            lambda: account.deploy(),
            timeout=60
        )

    await meta_call(
        lambda: account.wait_connected(),
        timeout=120
    )

    connection = account.get_rpc_connection()

    try:

        await meta_call(
            lambda: connection.connect(),
            timeout=120
        )

        await meta_call(
            lambda: connection.wait_synchronized(),
            timeout=120
        )

        notify(
            f"RIO GOLD {VERSION} CONNECTED\n"
            "M15: INFORMATION ONLY\n"
            "M5 FAST TREND: ACTIVE\n"
            "M1 FAST SCALP: ACTIVE\n"
            "M1 FAST RETEST: ACTIVE\n"
            "NO CHASE: ACTIVE\n"
            "SHARED BE: ACTIVE\n"
            f"{BATCH_SIZE} x {LOT_SIZE:.2f} LOT\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        while True:

            try:

                all_positions = await get_positions(
                    connection
                )

                managed = reconcile_state(
                    state,
                    all_positions
                )

                if all_positions:

                    if managed:

                        market = await get_market(
                            connection
                        )

                        await protect_positions(
                            connection,
                            managed,
                            state,
                            market
                        )

                    await asyncio.sleep(
                        PROTECTION_LOOP_SECONDS
                    )
                    continue

                if (
                    state["halted"]
                    or state["order_uncertain"]
                ):

                    print(
                        "TRADING HALTED - MANUAL REVIEW",
                        flush=True
                    )

                    await asyncio.sleep(5)
                    continue

                if (
                    time.time()
                    - state["last_trade_time"]
                    < COOLDOWN_SECONDS
                ):
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                candles = await get_candles(region)

                if not candles:
                    await asyncio.sleep(5)
                    continue

                candle_time = candles[-1]["time"]

                if candle_time == state["last_candle"]:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                state["last_candle"] = candle_time
                save_state(state)

                atr = calculate_atr(candles)

                if not atr or atr <= 0:
                    continue

                update_setup_lock(
                    state, candles, atr
                )

                m15_side = m15_direction(candles)

                side = m5_direction(candles)

                if not side:
                    print(
                        "WAIT: M5 FAST TREND NOT READY",
                        flush=True
                    )
                    continue

                signal = m1_trigger(
                    candles, side, atr
                )

                if not signal:
                    print(
                        "WAIT: M1 FAST SCALP NOT READY",
                        side,
                        flush=True
                    )
                    continue

                if (
                    state["locked_side"] == side
                    and not state["setup_released"]
                ):
                    print(
                        "WAIT: FRESH SETUP LOCK",
                        flush=True
                    )
                    continue

                anchor = protective_swing(
                    candles, side
                )

                signal_key = (
                    f"{candle_time}:"
                    f"{side}:"
                    f"{signal['mode']}:"
                    f"{signal['close']:.2f}"
                )

                if signal_key == state["last_signal"]:
                    continue

                state["last_signal"] = signal_key
                save_state(state)

                print(
                    f"RIO GOLD {VERSION} SETUP READY",
                    side,
                    signal["mode"],
                    f"M15={m15_side}",
                    f"ATR={atr:.2f}",
                    flush=True
                )

                await open_batch(
                    connection,
                    state,
                    side,
                    signal,
                    anchor,
                    atr
                )

                await asyncio.sleep(LOOP_SECONDS)

            except Exception:

                print(
                    "LOOP ERROR:",
                    traceback.format_exc(),
                    flush=True
                )

                await asyncio.sleep(5)

    finally:

        try:
            await asyncio.wait_for(
                connection.close(),
                timeout=10
            )
        except Exception:
            pass


async def main():

    keep_alive()

    if not M_TOKEN or not M_ACC:

        telegram(
            f"RIO GOLD {VERSION} ERROR\n"
            "M_TOKEN OR M_ACC MISSING"
        )
        return

    state = load_state()

    telegram(
        f"RIOBOT GOLD {VERSION} START\n"
        "FAST SCALP V14.17 ACTIVE\n"
        "M15 INFORMATION ONLY\n"
        "M5 FAST TREND\n"
        "M1 FAST SCALP + FAST RETEST\n"
        "NO CHASE ACTIVE\n"
        "BROKER SL/TP CHECK ACTIVE\n"
        "SHARED BE ACTIVE\n"
        "BE1: 0.35R -> +0.15\n"
        "BE2: 0.40R -> +0.22R\n"
        "BE3: 0.60R -> +0.38R\n"
        "TRAILING: OFF\n"
        f"{BATCH_SIZE} POSITIONS / ONE SIGNAL\n"
        f"LOT EACH: {LOT_SIZE:.2f}\n"
        f"TOTAL LOT: {BATCH_SIZE * LOT_SIZE:.2f}\n"
        "TP1: 10 x 0.60R\n"
        "TP2: 5 x 0.90R\n"
        "TP3: 5 x 1.20R\n"
        f"LIVE: {ENABLE_TRADING}"
    )

    while True:

        try:
            await bot_session(state)

        except Exception:

            print(
                "SESSION ERROR:",
                traceback.format_exc(),
                flush=True
            )

            await asyncio.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
