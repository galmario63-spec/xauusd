
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
# RIOBOT GOLD V14.20 - FAST M1 SCALP
# M1 ENTRY / M5 CONTEXT / SHARED BE
# =====================================================

VERSION = "V14.20"
SYMBOL = "XAUUSD"
COMMENT_PREFIX = "RIOGOLDV14"

LOT_SIZE = 0.25
BATCH_SIZE = 20
MAX_TRADES = 20

TP_GROUPS = [
    ("TP1", 10, 0.60),
    ("TP2", 5, 0.90),
    ("TP3", 5, 1.20),
]

ENABLE_TRADING = True

LOOP_SECONDS = 2
COOLDOWN_SECONDS = 180
RECONNECT_SECONDS = 3

PRICE_TIMEOUT = 5
POSITION_TIMEOUT = 8
ORDER_TIMEOUT = 20
HISTORY_TIMEOUT = 12

M1_HISTORY_LIMIT = 350
MIN_M1_HISTORY = 250
MAX_SIGNAL_AGE = 125
CANDLE_CACHE_SECONDS = 8
SPEC_CACHE_SECONDS = 300

ATR_PERIOD = 14
MAX_SPREAD = 0.50

MAX_FORWARD_ATR = 0.40
MAX_ADVERSE_ATR = 0.20
BATCH_FORWARD_ATR = 0.55

MIN_BODY_RATIO = 0.30
MAX_CANDLE_ATR = 2.20
MIN_BREAK_ATR = 0.03
MAX_EMA_DISTANCE_ATR = 0.90

MIN_SL_DISTANCE = 1.80
MAX_SL_DISTANCE = 4.50
SL_ATR_MULT = 1.40

BE1_TRIGGER = 0.35
BE2_TRIGGER = 0.40
BE3_TRIGGER = 0.60

BE1_LOCK = 0.15
BE2_LOCK = 0.22
BE3_LOCK = 0.38

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

STATE_DIR = os.getenv("RIO_STATE_DIR", "/tmp")
STATE_FILE = os.path.join(
    STATE_DIR, "rio_gold_v14.json"
)

app = Flask(__name__)

spec_cache = {"value": None, "time": 0}
candle_cache = {"value": None, "time": 0}


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
            data={"chat_id": T_CHAT, "text": message},
            timeout=5
        )
    except Exception as e:
        print("TELEGRAM ERROR", e, flush=True)


def notify(message):
    asyncio.create_task(
        asyncio.to_thread(telegram, message)
    )


def default_state():
    return {
        "positions": {},
        "last_trade_time": 0,
        "last_signal": None,
        "batch_be_stage": 0,
        "batch_in_progress": False,
        "order_uncertain": False,
        "halted": False
    }


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"

    with open(tmp, "w") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, STATE_FILE)


def load_state():
    state = default_state()

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)

            for key in state:
                if key in saved:
                    state[key] = saved[key]

        except Exception:
            state["halted"] = True
            state["order_uncertain"] = True

    if state["batch_in_progress"]:
        state["halted"] = True
        state["order_uncertain"] = True

    return state


async def meta_call(factory, timeout=12):
    return await asyncio.wait_for(
        factory(), timeout=timeout
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
    now_mono = time.monotonic()

    if (
        candle_cache["value"] is not None
        and now_mono - candle_cache["time"]
        < CANDLE_CACHE_SECONDS
    ):
        return candle_cache["value"]

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
            timeout=HISTORY_TIMEOUT
        )
        response.raise_for_status()
        return response.json()

    raw = await asyncio.to_thread(fetch)

    if not isinstance(raw, list):
        return None

    by_time = {}

    for item in raw:
        try:
            dt = parse_time(item["time"])
            age = (now - dt).total_seconds()

            # Use closed M1 candles only.
            if age < 61:
                continue

            by_time[dt.isoformat()] = {
                "time": dt.isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            }
        except (KeyError, TypeError, ValueError):
            continue

    candles = [
        by_time[key]
        for key in sorted(by_time)
    ]

    if len(candles) < MIN_M1_HISTORY:
        return None

    age = (
        now - parse_time(candles[-1]["time"])
    ).total_seconds()

    if age > MAX_SIGNAL_AGE:
        print("WAIT: STALE M1", age, flush=True)
        return None

    recent = candles[-30:]

    for a, b in zip(recent[:-1], recent[1:]):
        gap = (
            parse_time(b["time"])
            - parse_time(a["time"])
        ).total_seconds()

        if gap != 60:
            print("WAIT: M1 GAP", gap, flush=True)
            return None

    candle_cache["value"] = candles
    candle_cache["time"] = time.monotonic()

    return candles


def ema(values, period):
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


def atr_value(candles):
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


def build_tf(candles, minutes):
    groups = {}

    for c in candles:
        dt = parse_time(c["time"])
        key = dt.replace(
            minute=dt.minute - dt.minute % minutes,
            second=0,
            microsecond=0
        ).isoformat()

        if key not in groups:
            groups[key] = {
                "time": key,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "count": 1
            }
        else:
            g = groups[key]
            g["high"] = max(g["high"], c["high"])
            g["low"] = min(g["low"], c["low"])
            g["close"] = c["close"]
            g["count"] += 1

    return sorted(
        [
            g for g in groups.values()
            if g["count"] == minutes
        ],
        key=lambda x: x["time"]
    )


def m5_context(candles):
    bars = build_tf(candles, 5)

    if len(bars) < 23:
        return None

    closes = [c["close"] for c in bars]
    e9 = ema(closes, 9)
    e20 = ema(closes, 20)

    if (
        closes[-1] > e9[-1] > e20[-1]
        and e9[-1] > e9[-2]
    ):
        return "BUY"

    if (
        closes[-1] < e9[-1] < e20[-1]
        and e9[-1] < e9[-2]
    ):
        return "SELL"

    return None


def fast_signal(candles, atr):
    if len(candles) < 30:
        return None

    c = candles[-1]
    p = candles[-2]

    rng = c["high"] - c["low"]

    if rng <= 0 or rng > MAX_CANDLE_ATR * atr:
        return None

    closes = [x["close"] for x in candles]
    e9 = ema(closes, 9)
    e20 = ema(closes, 20)

    body = abs(c["close"] - c["open"]) / rng

    if body < MIN_BODY_RATIO:
        return None

    if abs(c["close"] - e9[-1]) > (
        MAX_EMA_DISTANCE_ATR * atr
    ):
        return None

    close_position = (
        c["close"] - c["low"]
    ) / rng

    # Fast M1 BUY.
    if (
        c["close"] > c["open"]
        and c["close"] > e9[-1] > e20[-1]
        and e9[-1] > e9[-2]
        and c["close"] >
        p["high"] + MIN_BREAK_ATR * atr
        and close_position >= 0.60
    ):
        side = "BUY"

    # Fast M1 SELL.
    elif (
        c["close"] < c["open"]
        and c["close"] < e9[-1] < e20[-1]
        and e9[-1] < e9[-2]
        and c["close"] <
        p["low"] - MIN_BREAK_ATR * atr
        and close_position <= 0.40
    ):
        side = "SELL"

    else:
        return None

    return {
        "side": side,
        "mode": "FAST M1 BREAKOUT",
        "close": c["close"],
        "ema9": e9[-1],
        "time": c["time"]
    }


def position_side(position):
    value = str(
        position.get("type", "")
    ).upper()

    if value in (
        "POSITION_TYPE_BUY",
        "ORDER_TYPE_BUY",
        "BUY",
        "0"
    ):
        return "BUY"

    if value in (
        "POSITION_TYPE_SELL",
        "ORDER_TYPE_SELL",
        "SELL",
        "1"
    ):
        return "SELL"

    return None


def is_managed(position):
    return str(
        position.get("comment") or ""
    ).upper().startswith(COMMENT_PREFIX)


async def get_positions(connection):
    positions = await meta_call(
        lambda: connection.get_positions(),
        POSITION_TIMEOUT
    )

    if not isinstance(positions, list):
        raise RuntimeError("INVALID POSITIONS")

    return [
        p for p in positions
        if str(p.get("symbol", "")).upper()
        == SYMBOL
    ]


async def get_spec(connection):
    now = time.monotonic()

    if (
        spec_cache["value"] is not None
        and now - spec_cache["time"]
        < SPEC_CACHE_SECONDS
    ):
        return spec_cache["value"]

    spec = await meta_call(
        lambda:
        connection.get_symbol_specification(SYMBOL),
        12
    )

    if not isinstance(spec, dict):
        raise RuntimeError("INVALID SPEC")

    spec_cache["value"] = spec
    spec_cache["time"] = time.monotonic()

    return spec


async def get_market(connection):
    spec = await get_spec(connection)

    price = await meta_call(
        lambda: connection.get_symbol_price(SYMBOL),
        PRICE_TIMEOUT
    )

    digits = int(spec.get("digits", 2))
    tick = float(
        spec.get("tickSize")
        or 10 ** (-digits)
    )
    point = float(
        spec.get("point")
        or 10 ** (-digits)
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    if tick <= 0 or bid <= 0 or ask <= bid:
        raise RuntimeError("INVALID PRICE")

    stops = float(spec.get("stopsLevel") or 0)
    freeze = float(spec.get("freezeLevel") or 0)

    minimum = (
        max(stops, freeze) * point
        + 2 * tick
    )

    return {
        "spec": spec,
        "bid": bid,
        "ask": ask,
        "tick": tick,
        "digits": digits,
        "minimum": max(minimum, 2 * tick)
    }


def normalize(value, market):
    tick = market["tick"]

    return round(
        round(value / tick) * tick,
        market["digits"]
    )


def levels_valid(side, sl, tp, market):
    minimum = market["minimum"]

    if side == "BUY":
        return (
            sl < market["bid"] - minimum
            and tp > market["ask"] + minimum
        )

    return (
        sl > market["ask"] + minimum
        and tp < market["bid"] - minimum
    )


def calculate_levels(
    side, entry, atr, market
):
    risk = max(
        MIN_SL_DISTANCE,
        SL_ATR_MULT * atr,
        market["minimum"] + market["tick"]
    )

    if risk > MAX_SL_DISTANCE:
        return None

    direction = 1 if side == "BUY" else -1

    sl = normalize(
        entry - direction * risk,
        market
    )

    tps = {}

    for name, count, rr in TP_GROUPS:
        tp = normalize(
            entry + direction * risk * rr,
            market
        )

        if not levels_valid(
            side, sl, tp, market
        ):
            return None

        tps[name] = tp

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
    changed = False

    for p in positions:
        pid = str(p["id"])

        if pid in state["positions"]:
            continue

        side = position_side(p)
        entry = float(p.get("openPrice") or 0)
        sl = float(p.get("stopLoss") or 0)
        tp = float(p.get("takeProfit") or 0)

        group, rr = group_from_comment(
            p.get("comment")
        )

        if not side or entry <= 0:
            continue

        if tp > 0 and rr:
            risk = abs(tp - entry) / rr
        elif sl > 0:
            risk = abs(entry - sl)
        else:
            notify(
                f"RIO GOLD POSITION {pid} "
                "MISSING SL/TP"
            )
            continue

        if risk <= 0:
            continue

        state["positions"][pid] = {
            "entry": entry,
            "risk": risk,
            "side": side,
            "tp_group": group
        }
        changed = True

    if changed:
        save_state(state)


def reconcile(state, all_positions):
    managed = [
        p for p in all_positions
        if is_managed(p)
    ]

    active_ids = {
        str(p["id"]) for p in managed
    }

    for pid in list(state["positions"]):
        if pid not in active_ids:
            state["positions"].pop(pid, None)
            state["last_trade_time"] = time.time()

    adopt_positions(state, managed)

    if not managed:
        state["batch_be_stage"] = 0

    save_state(state)
    return managed


async def protect_positions(
    connection, managed, state, market
):
    if not managed:
        return

    ratios = []

    for p in managed:
        pid = str(p["id"])
        info = state["positions"].get(pid)

        if not info:
            return

        side = position_side(p)
        entry = float(p.get("openPrice") or 0)
        risk = float(info.get("risk") or 0)

        if not side or entry <= 0 or risk <= 0:
            return

        profit = (
            market["bid"] - entry
            if side == "BUY"
            else entry - market["ask"]
        )

        ratios.append(profit / risk)

    weakest = min(ratios)

    new_stage = 0

    if weakest >= BE3_TRIGGER:
        new_stage = 3
    elif weakest >= BE2_TRIGGER:
        new_stage = 2
    elif weakest >= BE1_TRIGGER:
        new_stage = 1

    old_stage = int(state["batch_be_stage"])

    if new_stage > old_stage:
        state["batch_be_stage"] = new_stage
        save_state(state)

        notify(
            f"RIO GOLD {VERSION} "
            f"SHARED BE{new_stage}"
        )

    stage = int(state["batch_be_stage"])

    if stage == 0:
        return

    for p in managed:
        pid = str(p["id"])
        info = state["positions"].get(pid)

        if not info:
            continue

        side = position_side(p)
        entry = float(p["openPrice"])
        risk = float(info["risk"])
        current_sl = float(
            p.get("stopLoss") or 0
        )
        tp = p.get("takeProfit")

        if current_sl <= 0:
            continue

        if stage == 1:
            lock = BE1_LOCK
        elif stage == 2:
            lock = max(
                BE2_LOCK * risk,
                BE1_LOCK
            )
        else:
            lock = max(
                BE3_LOCK * risk,
                BE1_LOCK
            )

        if side == "BUY":
            wanted = normalize(
                entry + lock, market
            )

            if wanted <= current_sl:
                continue

            if wanted >= (
                market["bid"] - market["minimum"]
            ):
                continue

        elif side == "SELL":
            wanted = normalize(
                entry - lock, market
            )

            if wanted >= current_sl:
                continue

            if wanted <= (
                market["ask"] + market["minimum"]
            ):
                continue
        else:
            continue

        try:
            await meta_call(
                lambda pid=pid,
                       wanted=wanted,
                       tp=tp:
                connection.modify_position(
                    pid, wanted, tp
                ),
                12
            )

            print(
                "BE PROTECTED", pid,
                wanted, flush=True
            )

        except Exception as e:
            notify(
                f"BE ERROR {pid}: {str(e)[:120]}"
            )


def signal_fresh(signal):
    age = (
        datetime.now(timezone.utc)
        - parse_time(signal["time"])
    ).total_seconds()

    return 60 <= age <= MAX_SIGNAL_AGE


def price_allowed(
    side, entry, signal, atr, market,
    batch=False
):
    direction = 1 if side == "BUY" else -1

    drift = direction * (
        entry - signal["close"]
    )

    forward = (
        BATCH_FORWARD_ATR
        if batch
        else MAX_FORWARD_ATR
    )

    if drift > forward * atr:
        return False, "FORWARD DRIFT"

    if drift < -MAX_ADVERSE_ATR * atr:
        return False, "ADVERSE DRIFT"

    spread = market["ask"] - market["bid"]

    if spread > MAX_SPREAD:
        return False, "SPREAD"

    return True, "OK"


async def verify_positions(
    connection, previous_ids, side,
    attempts=8
):
    found = []

    for _ in range(attempts):
        positions = await get_positions(connection)

        found = [
            p for p in positions
            if (
                is_managed(p)
                and str(p["id"]) not in previous_ids
                and position_side(p) == side
            )
        ]

        if len(found) >= BATCH_SIZE:
            break

        await asyncio.sleep(0.5)

    return found


async def interrupt_batch(
    connection, state, previous_ids,
    side, sent, reason, uncertain
):
    try:
        found = await verify_positions(
            connection, previous_ids, side
        )

        adopt_positions(state, found)

        state["last_trade_time"] = time.time()
        state["batch_in_progress"] = False
        state["halted"] = bool(uncertain)
        state["order_uncertain"] = bool(uncertain)

        save_state(state)

        notify(
            f"RIO GOLD {VERSION} "
            "BATCH INTERRUPTED\n"
            f"SENT {sent}/{BATCH_SIZE}\n"
            f"OPEN {len(found)}\n"
            f"REASON {reason}\n"
            + (
                "MANUAL REVIEW"
                if uncertain
                else "NO AUTOMATIC TOP-UP"
            )
        )

    except Exception as e:
        state["halted"] = True
        state["order_uncertain"] = True
        save_state(state)

        notify(
            "RIO GOLD BATCH VERIFY ERROR\n"
            f"{str(e)[:120]}"
        )


async def open_batch(
    connection, state, signal, atr
):
    if (
        state["halted"]
        or state["order_uncertain"]
        or state["batch_in_progress"]
    ):
        return False

    if not signal_fresh(signal):
        return False

    existing = await get_positions(connection)

    # Do not mix with manual or other bot trades.
    if existing:
        print(
            "ENTRY BLOCKED: EXISTING POSITIONS",
            flush=True
        )
        return False

    market = await get_market(connection)

    side = signal["side"]
    entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )

    allowed, reason = price_allowed(
        side, entry, signal, atr, market
    )

    if not allowed:
        print(
            "ENTRY BLOCKED:", reason,
            flush=True
        )
        return False

    if not signal_fresh(signal):
        return False

    levels = calculate_levels(
        side, entry, atr, market
    )

    if levels is None:
        print(
            "ENTRY BLOCKED: SL/TP",
            flush=True
        )
        return False

    spec = market["spec"]
    min_volume = float(
        spec.get("minVolume") or 0.01
    )
    volume_step = float(
        spec.get("volumeStep") or 0.01
    )

    if (
        LOT_SIZE < min_volume
        or volume_step <= 0
        or abs(
            LOT_SIZE / volume_step
            - round(LOT_SIZE / volume_step)
        ) > 1e-7
    ):
        notify("INVALID LOT SIZE")
        return False

    if not ENABLE_TRADING:
        notify(
            f"TEST SIGNAL {side}\n"
            f"SL {levels['sl']}\n"
            f"TP {levels['tps']}"
        )
        return False

    previous_ids = {
        str(p["id"]) for p in existing
    }

    state["batch_in_progress"] = True
    state["batch_be_stage"] = 0
    save_state(state)

    sent = 0
    uncertain = False
    failure = None

    try:
        for group_name, count, rr in TP_GROUPS:
            for _ in range(count):
                live_market = await get_market(
                    connection
                )

                live_entry = (
                    live_market["ask"]
                    if side == "BUY"
                    else live_market["bid"]
                )

                allowed, reason = price_allowed(
                    side,
                    live_entry,
                    signal,
                    atr,
                    live_market,
                    batch=(sent > 0)
                )

                if not allowed:
                    failure = reason
                    break

                if not signal_fresh(signal):
                    failure = "STALE SIGNAL"
                    break

                sl = levels["sl"]
                tp = levels["tps"][group_name]

                if not levels_valid(
                    side, sl, tp, live_market
                ):
                    failure = "INVALID LIVE SL/TP"
                    break

                comment = (
                    f"{COMMENT_PREFIX}_"
                    f"{group_name}_{sent + 1:02d}"
                )

                options = {"comment": comment}

                try:
                    if side == "BUY":
                        result = await meta_call(
                            lambda:
                            connection.create_market_buy_order(
                                SYMBOL,
                                LOT_SIZE,
                                sl,
                                tp,
                                options
                            ),
                            ORDER_TIMEOUT
                        )
                    else:
                        result = await meta_call(
                            lambda:
                            connection.create_market_sell_order(
                                SYMBOL,
                                LOT_SIZE,
                                sl,
                                tp,
                                options
                            ),
                            ORDER_TIMEOUT
                        )

                except Exception as e:
                    uncertain = True
                    failure = (
                        "ORDER UNCERTAIN: "
                        + str(e)[:120]
                    )
                    break

                sent += 1

                print(
                    f"ORDER {sent}/{BATCH_SIZE}",
                    result, flush=True
                )

            if failure:
                break

        if failure:
            await interrupt_batch(
                connection,
                state,
                previous_ids,
                side,
                sent,
                failure,
                uncertain
            )
            return False

        found = await verify_positions(
            connection, previous_ids, side
        )

        adopt_positions(state, found)

        state["last_trade_time"] = time.time()
        state["batch_in_progress"] = False

        if len(found) != BATCH_SIZE:
            state["halted"] = True
            state["order_uncertain"] = True
            save_state(state)

            notify(
                f"RIO GOLD {VERSION}\n"
                "BATCH MISMATCH\n"
                f"OPEN {len(found)}/{BATCH_SIZE}\n"
                "CHECK MT5"
            )
            return False

        state["halted"] = False
        state["order_uncertain"] = False
        save_state(state)

        notify(
            f"RIO GOLD {VERSION} BUY/SELL\n"
            f"SIDE {side}\n"
            f"OPEN {len(found)}/{BATCH_SIZE}\n"
            f"LOT EACH {LOT_SIZE:.2f}\n"
            f"TOTAL {LOT_SIZE * BATCH_SIZE:.2f}"
        )

        return True

    except Exception as e:
        await interrupt_batch(
            connection,
            state,
            previous_ids,
            side,
            sent,
            str(e)[:120],
            True
        )
        return False


async def bot_session(state):
    api = MetaApi(M_TOKEN)

    account = await meta_call(
        lambda:
        api.metatrader_account_api.get_account(
            M_ACC
        ),
        40
    )

    region = (
        getattr(account, "region", None)
        or "london"
    )

    if str(account.state).upper() != "DEPLOYED":
        await meta_call(
            lambda: account.deploy(), 60
        )

    await meta_call(
        lambda: account.wait_connected(), 120
    )

    connection = account.get_rpc_connection()

    try:
        await meta_call(
            lambda: connection.connect(), 120
        )

        await meta_call(
            lambda:
            connection.wait_synchronized(), 120
        )

        notify(
            f"RIO GOLD {VERSION} CONNECTED\n"
            "FAST M1 BUY + SELL\n"
            "M5 CONTEXT ONLY\n"
            "NO CHASE ACTIVE\n"
            "SHARED BE ACTIVE\n"
            f"{BATCH_SIZE} x {LOT_SIZE:.2f}\n"
            f"LIVE {ENABLE_TRADING}"
        )

        while True:
            try:
                all_positions = await get_positions(
                    connection
                )

                managed = reconcile(
                    state, all_positions
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

                    await asyncio.sleep(1)
                    continue

                if (
                    state["halted"]
                    or state["order_uncertain"]
                    or state["batch_in_progress"]
                ):
                    print(
                        "TRADING HALTED - CHECK MT5",
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
                    await asyncio.sleep(3)
                    continue

                atr = atr_value(candles)

                if not atr or atr <= 0:
                    await asyncio.sleep(2)
                    continue

                signal = fast_signal(candles, atr)

                if not signal:
                    print(
                        "WAIT: FAST M1 SIGNAL",
                        flush=True
                    )
                    await asyncio.sleep(2)
                    continue

                signal_key = (
                    f"{signal['time']}:"
                    f"{signal['side']}"
                )

                # One batch per M1 signal.
                if signal_key == state["last_signal"]:
                    await asyncio.sleep(2)
                    continue

                print(
                    "FAST SIGNAL",
                    signal["side"],
                    "M5",
                    m5_context(candles),
                    "ATR",
                    round(atr, 2),
                    flush=True
                )

                opened = await open_batch(
                    connection,
                    state,
                    signal,
                    atr
                )

                if opened:
                    state["last_signal"] = signal_key
                    save_state(state)

                await asyncio.sleep(LOOP_SECONDS)

            except Exception:
                print(
                    "LOOP ERROR",
                    traceback.format_exc(),
                    flush=True
                )
                await asyncio.sleep(3)

    finally:
        try:
            await asyncio.wait_for(
                connection.close(), 10
            )
        except Exception:
            pass


async def main():
    keep_alive()

    if not M_TOKEN or not M_ACC:
        telegram(
            f"RIO GOLD {VERSION}\n"
            "M_TOKEN OR M_ACC MISSING"
        )
        return

    state = load_state()

    telegram(
        f"RIOBOT GOLD {VERSION} START\n"
        "FAST M1 SCALP\n"
        "BUY + SELL\n"
        "M5 CONTEXT ONLY\n"
        "NO CHASE\n"
        "SHARED BE\n"
        "NO ORDER RETRIES\n"
        f"LOT EACH {LOT_SIZE:.2f}\n"
        f"POSITIONS {BATCH_SIZE}\n"
        f"TOTAL LOT {LOT_SIZE * BATCH_SIZE:.2f}\n"
        "TP1 10 x 0.60R\n"
        "TP2 5 x 0.90R\n"
        "TP3 5 x 1.20R\n"
        f"LIVE {ENABLE_TRADING}"
    )

    while True:
        try:
            await bot_session(state)
        except Exception:
            print(
                "SESSION ERROR",
                traceback.format_exc(),
                flush=True
            )
            await asyncio.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
