import os
import json
import time
import asyncio
import traceback
import requests

from datetime import datetime, timezone
from threading import Thread

from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =====================================================
# RIOBOT GOLD V12 - LOCAL SWING ZONES
# =====================================================

SYMBOL = "XAUUSD"
COMMENT = "RIO GOLD V12 SWING ZONES ATR EMA MOM"

LOT_SIZE = 0.01
BATCH_SIZE = 4
MAX_TRADES = 4

ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "false")
    .strip().lower() == "true"
)

LOOP_SECONDS = 2

RECONNECT_SECONDS = 5

RPC_TIMEOUT = 20
RPC_RETRIES = 3
RPC_RETRY_DELAY = 2

CONNECT_TIMEOUT = 90
ORDER_TIMEOUT = 40

COOLDOWN_SECONDS = 180


# =====================================================
# M1 / ZONES / ATR
# =====================================================

ZONE_LOOKBACK = 35
ATR_PERIOD = 14

ZONE_ATR_TOLERANCE = 0.40
MIN_ZONE_TOLERANCE = 0.35

MIN_BODY_RATIO = 0.25

MAX_SIGNAL_RANGE_ATR = 2.20
MAX_ENTRY_DRIFT_ATR = 0.50

# ZMENA:
# vstup musi ostat blizko support/resistance zony
MAX_ZONE_DISTANCE_ATR = 0.80

MAX_SPREAD = 0.40


# =====================================================
# LOCAL SWING ZONES
# =====================================================

SWING_LEFT = 2
SWING_RIGHT = 2

MAX_SWINGS = 12

SWING_CLUSTER_ATR = 0.45

MIN_ZONE_TOUCHES = 1

OPPOSITE_ZONE_BLOCK_ATR = 0.80

REJECTION_WICK_MIN = 0.15


# =====================================================
# FILTRE
# =====================================================

EMA_PERIOD = 50

MOMENTUM_LOOKBACK = 3
MOMENTUM_MIN_SAME_DIRECTION = 2

BROKER_BREAK_HOUR_UTC = 22
BROKER_BREAK_MINUTE_UTC = 0
BREAK_BLOCK_BEFORE_MINUTES = 15


# =====================================================
# SL / TP
# =====================================================

SL_ATR_BUFFER = 1.20

MIN_SL_DISTANCE = 2.50
MAX_SL_DISTANCE = 8.00

TP_RR = 1.50


# =====================================================
# BREAK EVEN
# =====================================================

BE_TRIGGER_RR = 0.70
BE_LOCK_RR = 0.10


# =====================================================
# NEWS
# =====================================================

NEWS_BEFORE = 15
NEWS_AFTER = 30

NEWS_REFRESH = 1800
NEWS_RETRY = 120
NEWS_MAX_AGE = 10800

NEWS_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)


# =====================================================
# ENV
# =====================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

STATE_DIR = os.getenv("RIO_STATE_DIR", "/tmp")

STATE_FILE = os.path.join(
    STATE_DIR,
    "rio_gold_v12.json"
)

news_cache = {
    "events": [],
    "updated": 0,
    "last_attempt": 0
}


# =====================================================
# RENDER
# =====================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIOBOT GOLD V12 SWING ZONES ACTIVE", 200


def keep_alive():

    def run():
        app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", "10000")),
            use_reloader=False
        )

    Thread(
        target=run,
        daemon=True
    ).start()


# =====================================================
# TELEGRAM
# =====================================================

def telegram(message):

    print(message, flush=True)

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
            timeout=6
        )

    except Exception as e:
        print(
            "TELEGRAM ERROR:",
            e,
            flush=True
        )


def notify(message):

    asyncio.create_task(
        asyncio.to_thread(
            telegram,
            message
        )
    )


# =====================================================
# STATE
# =====================================================

def default_state():

    return {
        "trade_count": 0,
        "positions": {},
        "last_trade_time": 0,
        "last_candle": None,
        "last_signal": None,
        "order_uncertain": False,
        "halted": False
    }


def save_state(state):

    os.makedirs(
        STATE_DIR,
        exist_ok=True
    )

    temp = STATE_FILE + ".tmp"

    with open(temp, "w") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())

    os.replace(
        temp,
        STATE_FILE
    )


def load_state():

    state = default_state()

    if not os.path.exists(STATE_FILE):

        print(
            "NEW V12 STATE - COUNTER ZERO",
            flush=True
        )

        return state

    try:

        with open(STATE_FILE) as f:
            saved = json.load(f)

        for key in state:
            if key in saved:
                state[key] = saved[key]

    except Exception as e:

        print(
            "STATE LOAD ERROR:",
            e,
            flush=True
        )

        state["halted"] = True

    return state


# =====================================================
# METAAPI - STABLE RPC
# =====================================================

async def meta_call(
    coroutine,
    timeout=RPC_TIMEOUT
):
    return await asyncio.wait_for(
        coroutine,
        timeout=timeout
    )


async def get_positions(connection):

    last_error = None

    for attempt in range(1, RPC_RETRIES + 1):

        try:

            result = await meta_call(
                connection.get_positions(),
                timeout=RPC_TIMEOUT
            )

            if not isinstance(result, list):
                raise RuntimeError(
                    "INVALID POSITIONS RESPONSE"
                )

            return [
                p for p in result
                if str(
                    p.get("symbol", "")
                ).upper() == SYMBOL
            ]

        except Exception as e:

            last_error = e

            print(
                f"POSITIONS RPC RETRY "
                f"{attempt}/{RPC_RETRIES}: "
                f"{type(e).__name__}: {e}",
                flush=True
            )

            if attempt < RPC_RETRIES:
                await asyncio.sleep(
                    RPC_RETRY_DELAY
                )

    raise RuntimeError(
        "METAAPI POSITIONS FAILED "
        f"AFTER {RPC_RETRIES} RETRIES: "
        f"{last_error}"
    )


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


# =====================================================
# M1 DATA
# =====================================================

async def get_candles(region):

    url = (
        "https://mt-market-data-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/"
        "historical-market-data/symbols/"
        f"{SYMBOL}/timeframes/1m/candles"
    )

    def fetch():

        response = requests.get(
            url,
            headers={
                "auth-token": M_TOKEN
            },
            timeout=12
        )

        if response.status_code != 200:

            raise RuntimeError(
                "M1 DATA HTTP "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            )

        return response.json()

    try:

        raw = await asyncio.wait_for(
            asyncio.to_thread(fetch),
            timeout=16
        )

    except Exception as e:

        raise RuntimeError(
            f"M1 DATA REQUEST ERROR: {e}"
        ) from e

    if not isinstance(raw, list):

        raise RuntimeError(
            "INVALID M1 DATA"
        )

    now = datetime.now(
        timezone.utc
    )

    candles = []

    for item in raw:

        try:

            dt = datetime.fromisoformat(
                str(item["time"]).replace(
                    "Z",
                    "+00:00"
                )
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            dt = dt.astimezone(
                timezone.utc
            )

            if (
                now - dt
            ).total_seconds() < 61:
                continue

            candles.append({
                "time": dt.isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

        except (
            KeyError,
            TypeError,
            ValueError
        ):
            continue

    candles = sorted(
        {
            c["time"]: c
            for c in candles
        }.values(),
        key=lambda c: c["time"]
    )

    if len(candles) < 70:

        raise RuntimeError(
            f"NOT ENOUGH M1 HISTORY: {len(candles)}"
        )

    last = datetime.fromisoformat(
        candles[-1]["time"]
    )

    if (
        now - last
    ).total_seconds() > 180:

        raise RuntimeError(
            "STALE M1 DATA"
        )

    return candles


# =====================================================
# ATR14
# =====================================================

def calculate_atr(candles):

    if len(candles) < ATR_PERIOD + 2:
        return None

    ranges = []

    for i in range(
        1,
        len(candles)
    ):

        candle = candles[i]
        previous = candles[i - 1]

        true_range = max(
            candle["high"] - candle["low"],
            abs(
                candle["high"]
                - previous["close"]
            ),
            abs(
                candle["low"]
                - previous["close"]
            )
        )

        ranges.append(
            true_range
        )

    return (
        sum(
            ranges[-ATR_PERIOD:]
        )
        / ATR_PERIOD
    )


# =====================================================
# M5 EMA50
# =====================================================

def build_m5_candles(candles):

    groups = {}

    for candle in candles:

        dt = datetime.fromisoformat(
            candle["time"]
        )

        minute = (
            dt.minute
            - dt.minute % 5
        )

        key = dt.replace(
            minute=minute,
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

            groups[key]["high"] = max(
                groups[key]["high"],
                candle["high"]
            )

            groups[key]["low"] = min(
                groups[key]["low"],
                candle["low"]
            )

            groups[key]["close"] = (
                candle["close"]
            )

            groups[key]["count"] += 1

    result = [
        c for c in groups.values()
        if c["count"] == 5
    ]

    return sorted(
        result,
        key=lambda c: c["time"]
    )


def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = (
        2.0 / (period + 1)
    )

    ema = (
        sum(values[:period])
        / period
    )

    for value in values[period:]:

        ema = (
            value * multiplier
            + ema * (1 - multiplier)
        )

    return ema


def m5_trend_filter(candles, side):

    m5 = build_m5_candles(
        candles
    )

    if len(m5) < EMA_PERIOD:

        return (
            False,
            None,
            None
        )

    closes = [
        c["close"]
        for c in m5
    ]

    ema50 = calculate_ema(
        closes,
        EMA_PERIOD
    )

    last_close = closes[-1]

    if ema50 is None:

        return (
            False,
            last_close,
            None
        )

    if side == "BUY":
        allowed = (
            last_close > ema50
        )
    else:
        allowed = (
            last_close < ema50
        )

    return (
        allowed,
        last_close,
        ema50
    )


# =====================================================
# M1 MOMENTUM
# =====================================================

def momentum_filter(candles, side):

    recent = candles[
        -MOMENTUM_LOOKBACK:
    ]

    if (
        len(recent)
        < MOMENTUM_LOOKBACK
    ):
        return False

    bullish = sum(
        1
        for c in recent
        if c["close"] > c["open"]
    )

    bearish = sum(
        1
        for c in recent
        if c["close"] < c["open"]
    )

    if side == "BUY":

        if (
            bearish
            >= MOMENTUM_MIN_SAME_DIRECTION
        ):
            return False

    else:

        if (
            bullish
            >= MOMENTUM_MIN_SAME_DIRECTION
        ):
            return False

    return True


# =====================================================
# BROKER BREAK
# =====================================================

def broker_break_blocked():

    now = datetime.now(
        timezone.utc
    )

    current_minutes = (
        now.hour * 60
        + now.minute
    )

    break_minutes = (
        BROKER_BREAK_HOUR_UTC * 60
        + BROKER_BREAK_MINUTE_UTC
    )

    start_block = (
        break_minutes
        - BREAK_BLOCK_BEFORE_MINUTES
    )

    return (
        start_block
        <= current_minutes
        < break_minutes
    )


# =====================================================
# LOCAL SWING SUPPORT / RESISTANCE
# =====================================================

def find_swing_levels(candles):

    history = candles[
        -(ZONE_LOOKBACK + 10):-3
    ]

    if len(history) < 10:
        return [], []

    swing_lows = []
    swing_highs = []

    for i in range(
        SWING_LEFT,
        len(history) - SWING_RIGHT
    ):

        candle = history[i]

        left = history[
            i - SWING_LEFT:i
        ]

        right = history[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if all(
            candle["low"] <= x["low"]
            for x in left + right
        ):
            swing_lows.append(
                candle["low"]
            )

        if all(
            candle["high"] >= x["high"]
            for x in left + right
        ):
            swing_highs.append(
                candle["high"]
            )

    return (
        swing_lows[-MAX_SWINGS:],
        swing_highs[-MAX_SWINGS:]
    )


def cluster_levels(
    levels,
    atr
):

    if not levels:
        return []

    max_distance = max(
        atr * SWING_CLUSTER_ATR,
        MIN_ZONE_TOLERANCE
    )

    clusters = []

    for level in levels:

        matched = False

        for cluster in clusters:

            center = sum(
                cluster
            ) / len(cluster)

            if (
                abs(level - center)
                <= max_distance
            ):
                cluster.append(level)
                matched = True
                break

        if not matched:
            clusters.append(
                [level]
            )

    result = []

    for cluster in clusters:

        if (
            len(cluster)
            >= MIN_ZONE_TOUCHES
        ):

            result.append({
                "price": (
                    sum(cluster)
                    / len(cluster)
                ),
                "touches": len(cluster)
            })

    return result


def detect_zones(candles, atr):

    swing_lows, swing_highs = (
        find_swing_levels(candles)
    )

    support_zones = cluster_levels(
        swing_lows,
        atr
    )

    resistance_zones = cluster_levels(
        swing_highs,
        atr
    )

    reference_price = candles[-1][
        "close"
    ]

    supports_below = [
        z
        for z in support_zones
        if z["price"] <= reference_price
    ]

    resistances_above = [
        z
        for z in resistance_zones
        if z["price"] >= reference_price
    ]

    if supports_below:

        support = max(
            supports_below,
            key=lambda z: z["price"]
        )["price"]

    else:

        history = candles[
            -(ZONE_LOOKBACK + 3):-3
        ]

        support = min(
            c["low"]
            for c in history
        )

    if resistances_above:

        resistance = min(
            resistances_above,
            key=lambda z: z["price"]
        )["price"]

    else:

        history = candles[
            -(ZONE_LOOKBACK + 3):-3
        ]

        resistance = max(
            c["high"]
            for c in history
        )

    tolerance = max(
        MIN_ZONE_TOLERANCE,
        atr * ZONE_ATR_TOLERANCE
    )

    return (
        support,
        resistance,
        tolerance
    )


# =====================================================
# SIGNAL - STRICT CURRENT-CANDLE ZONE REJECTION
# =====================================================

def get_signal(
    candles,
    support,
    resistance,
    tolerance,
    atr
):

    b = candles[-2]
    c = candles[-1]

    range_b = (
        b["high"] - b["low"]
    )

    range_c = (
        c["high"] - c["low"]
    )

    if (
        range_b <= 0
        or range_c <= 0
    ):
        return (
            None,
            None,
            "ZERO RANGE"
        )

    if (
        range_b
        > atr * MAX_SIGNAL_RANGE_ATR
        or
        range_c
        > atr * MAX_SIGNAL_RANGE_ATR
    ):
        return (
            None,
            None,
            "IMPULSE"
        )

    body_c = (
        abs(
            c["close"] - c["open"]
        )
        / range_c
    )

    lower_wick_c = (
        min(
            c["open"],
            c["close"]
        )
        - c["low"]
    ) / range_c

    upper_wick_c = (
        c["high"]
        - max(
            c["open"],
            c["close"]
        )
    ) / range_c

    # -------------------------------------------------
    # BUY
    # Aktualna C sviecka MUSI byt priamo pri supporte.
    # Stary dotyk A/B uz nestaci.
    # -------------------------------------------------

    buy_zone_touch = (
        support is not None
        and c["low"] <= support + tolerance
        and c["high"] >= support - tolerance
    )

    buy_confirmation = (
        c["close"] > c["open"]
        and body_c >= MIN_BODY_RATIO
        and c["close"] > support
        and (
            c["close"] > b["close"]
            or lower_wick_c
            >= REJECTION_WICK_MIN
        )
    )

    if (
        buy_zone_touch
        and buy_confirmation
    ):

        distance = (
            c["close"] - support
        )

        room_to_resistance = (
            resistance - c["close"]
            if resistance is not None
            else float("inf")
        )

        if (
            distance >= 0
            and distance
            <= atr * MAX_ZONE_DISTANCE_ATR
            and room_to_resistance
            >= atr * OPPOSITE_ZONE_BLOCK_ATR
        ):
            return (
                "BUY",
                support,
                "STRICT SUPPORT REJECTION BUY"
            )

    # -------------------------------------------------
    # SELL
    # Aktualna C sviecka MUSI byt priamo pri resistance.
    # Stary dotyk A/B uz nestaci.
    # -------------------------------------------------

    sell_zone_touch = (
        resistance is not None
        and c["high"] >= resistance - tolerance
        and c["low"] <= resistance + tolerance
    )

    sell_confirmation = (
        c["close"] < c["open"]
        and body_c >= MIN_BODY_RATIO
        and c["close"] < resistance
        and (
            c["close"] < b["close"]
            or upper_wick_c
            >= REJECTION_WICK_MIN
        )
    )

    if (
        sell_zone_touch
        and sell_confirmation
    ):

        distance = (
            resistance - c["close"]
        )

        room_to_support = (
            c["close"] - support
            if support is not None
            else float("inf")
        )

        if (
            distance >= 0
            and distance
            <= atr * MAX_ZONE_DISTANCE_ATR
            and room_to_support
            >= atr * OPPOSITE_ZONE_BLOCK_ATR
        ):
            return (
                "SELL",
                resistance,
                "STRICT RESISTANCE REJECTION SELL"
            )

    return (
        None,
        None,
        "WAITING"
    )


# =====================================================
# NEWS
# =====================================================

def fetch_news():

    response = requests.get(
        NEWS_URL,
        timeout=12
    )

    response.raise_for_status()

    raw = response.json()

    if not isinstance(raw, list):
        raise RuntimeError(
            "INVALID NEWS RESPONSE"
        )

    events = []

    for event in raw:

        currency = str(
            event.get("country")
            or event.get("currency")
            or ""
        ).upper()

        impact = str(
            event.get("impact", "")
        ).lower()

        if currency != "USD":
            continue

        if (
            "high" not in impact
            and "red" not in impact
        ):
            continue

        try:

            dt = datetime.fromisoformat(
                str(
                    event["date"]
                ).replace(
                    "Z",
                    "+00:00"
                )
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            events.append(
                dt.timestamp()
            )

        except (
            KeyError,
            TypeError,
            ValueError
        ):
            continue

    return events


async def news_blocked():

    now = time.time()

    refresh_due = (
        now
        - news_cache["updated"]
        >= NEWS_REFRESH
    )

    retry_allowed = (
        now
        - news_cache["last_attempt"]
        >= NEWS_RETRY
    )

    if (
        refresh_due
        and retry_allowed
    ):

        news_cache["last_attempt"] = now

        try:

            events = await asyncio.wait_for(
                asyncio.to_thread(
                    fetch_news
                ),
                timeout=16
            )

            news_cache["events"] = events
            news_cache["updated"] = (
                time.time()
            )

        except Exception as e:

            print(
                "NEWS ERROR:",
                e,
                flush=True
            )

    if (
        now
        - news_cache["updated"]
        > NEWS_MAX_AGE
    ):
        return True

    for event_time in news_cache["events"]:

        if (
            event_time
            - NEWS_BEFORE * 60
            <= now
            <= event_time
            + NEWS_AFTER * 60
        ):
            return True

    return False


# =====================================================
# MARKET
# =====================================================

async def get_market(connection):

    spec = await meta_call(
        connection.get_symbol_specification(
            SYMBOL
        )
    )

    price = await meta_call(
        connection.get_symbol_price(
            SYMBOL
        )
    )

    if not spec or not price:
        raise RuntimeError(
            "MARKET NOT READY"
        )

    digits = int(
        spec.get(
            "digits",
            2
        )
    )

    tick = float(
        spec.get("tickSize")
        or 10 ** (-digits)
    )

    bid = float(
        price["bid"]
    )

    ask = float(
        price["ask"]
    )

    if (
        tick <= 0
        or bid <= 0
        or ask <= bid
    ):
        raise RuntimeError(
            "INVALID MARKET PRICE"
        )

    return {
        "spec": spec,
        "bid": bid,
        "ask": ask,
        "tick": tick,
        "digits": digits
    }


def normalize(value, market):

    tick = market["tick"]

    return round(
        round(
            value / tick
        ) * tick,
        market["digits"]
    )


# =====================================================
# SL / TP
# =====================================================

def get_levels(
    side,
    entry,
    zone,
    atr,
    market
):

    buffer = max(
        atr * SL_ATR_BUFFER,
        market["tick"] * 5
    )

    if side == "BUY":
        raw_sl = (
            zone - buffer
        )
    else:
        raw_sl = (
            zone + buffer
        )

    raw_risk = abs(
        entry - raw_sl
    )

    risk = max(
        raw_risk,
        MIN_SL_DISTANCE
    )

    if risk > MAX_SL_DISTANCE:
        return (
            None,
            "SL TOO LARGE"
        )

    if side == "BUY":

        sl = entry - risk
        tp = entry + risk * TP_RR

    else:

        sl = entry + risk
        tp = entry - risk * TP_RR

    sl = normalize(
        sl,
        market
    )

    tp = normalize(
        tp,
        market
    )

    actual_risk = abs(
        entry - sl
    )

    if actual_risk <= 0:
        return (
            None,
            "INVALID RISK"
        )

    return {
        "sl": sl,
        "tp": tp,
        "risk": actual_risk
    }, None


# =====================================================
# VERIFY POSITION
# =====================================================

async def verify_new_position(
    connection,
    previous_ids,
    side
):

    for attempt in range(6):

        await asyncio.sleep(1)

        positions = await get_positions(
            connection
        )

        new_positions = [
            p for p in positions
            if str(p["id"])
            not in previous_ids
        ]

        matches = [
            p for p in new_positions
            if (
                position_side(p) == side
                and
                abs(
                    float(
                        p.get(
                            "volume",
                            0
                        )
                    )
                    - LOT_SIZE
                ) < 0.000001
            )
        ]

        if len(matches) == 1:
            return matches[0]

        if len(new_positions) > 1:
            raise RuntimeError(
                "AMBIGUOUS NEW POSITIONS"
            )

    raise RuntimeError(
        "NEW POSITION NOT VERIFIED"
    )


# =====================================================
# OPEN BATCH
# =====================================================

async def open_batch(
    connection,
    side,
    zone,
    atr,
    signal_close,
    state
):

    if (
        state["halted"]
        or state["order_uncertain"]
    ):
        return

    positions = await get_positions(
        connection
    )

    if positions:
        return

    state["trade_count"] = 0
    save_state(state)

    market = await get_market(
        connection
    )

    spread = (
        market["ask"]
        - market["bid"]
    )

    if spread > MAX_SPREAD:

        print(
            "SPREAD TOO HIGH:",
            spread,
            flush=True
        )
        return

    spec = market["spec"]

    min_volume = float(
        spec.get("minVolume")
        or 0.01
    )

    volume_step = float(
        spec.get("volumeStep")
        or 0.01
    )

    if (
        LOT_SIZE < min_volume
        or volume_step <= 0
        or abs(
            LOT_SIZE / volume_step
            - round(
                LOT_SIZE / volume_step
            )
        ) > 1e-7
    ):

        notify(
            "V12 INVALID LOT"
        )
        return

    entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )

    if (
        abs(
            entry - signal_close
        )
        > atr
        * MAX_ENTRY_DRIFT_ATR
    ):

        print(
            "ENTRY DRIFT TOO HIGH",
            flush=True
        )
        return

    zone_distance = (
        entry - zone
        if side == "BUY"
        else zone - entry
    )

    if (
        zone_distance < 0
        or zone_distance
        > atr
        * MAX_ZONE_DISTANCE_ATR
    ):

        print(
            "ENTRY TOO FAR FROM ZONE",
            flush=True
        )
        return

    levels, reason = get_levels(
        side,
        entry,
        zone,
        atr,
        market
    )

    if levels is None:

        print(
            "ENTRY BLOCKED:",
            reason,
            flush=True
        )
        return

    sl = levels["sl"]
    tp = levels["tp"]

    point = float(
        spec.get("point")
        or 10 ** (-market["digits"])
    )

    stops = float(
        spec.get("stopsLevel")
        or 0
    )

    min_distance = (
        stops * point
        + market["tick"]
    )

    if side == "BUY":

        if (
            market["bid"] - sl
            <= min_distance
        ):
            return

        if (
            tp - market["bid"]
            <= min_distance
        ):
            return

    else:

        if (
            sl - market["ask"]
            <= min_distance
        ):
            return

        if (
            market["ask"] - tp
            <= min_distance
        ):
            return

    if not ENABLE_TRADING:

        notify(
            "RIO V12 TEST SIGNAL\n"
            f"SIDE: {side}\n"
            f"4 x {LOT_SIZE} LOT\n"
            f"ZONE: {zone:.2f}\n"
            f"ATR: {atr:.2f}\n"
            f"SL: {sl}\n"
            f"TP: {tp}"
        )
        return

    state["order_uncertain"] = True
    save_state(state)

    notify(
        "RIO V12 BATCH START\n"
        f"SIDE: {side}\n"
        f"TARGET: {BATCH_SIZE}\n"
        f"LOT EACH: {LOT_SIZE}\n"
        f"SL: {sl}\n"
        f"TP: {tp}"
    )

    previous_ids = set()

    try:

        positions = await get_positions(
            connection
        )

        previous_ids = {
            str(p["id"])
            for p in positions
        }

        if previous_ids:
            raise RuntimeError(
                "POSITIONS APPEARED BEFORE BATCH"
            )

        for number in range(
            1,
            BATCH_SIZE + 1
        ):

            options = {
                "comment": COMMENT
            }

            if side == "BUY":

                result = await meta_call(
                    connection.create_market_buy_order(
                        SYMBOL,
                        LOT_SIZE,
                        sl,
                        tp,
                        options
                    ),
                    timeout=ORDER_TIMEOUT
                )

            else:

                result = await meta_call(
                    connection.create_market_sell_order(
                        SYMBOL,
                        LOT_SIZE,
                        sl,
                        tp,
                        options
                    ),
                    timeout=ORDER_TIMEOUT
                )

            print(
                "ORDER",
                number,
                result,
                flush=True
            )

            position = await verify_new_position(
                connection,
                previous_ids,
                side
            )

            pid = str(
                position["id"]
            )

            actual_entry = float(
                position.get("openPrice")
                or entry
            )

            actual_sl = float(
                position.get("stopLoss")
                or 0
            )

            actual_tp = float(
                position.get("takeProfit")
                or 0
            )

            if (
                actual_sl <= 0
                or actual_tp <= 0
            ):
                raise RuntimeError(
                    "BROKER SL OR TP MISSING"
                )

            actual_risk = abs(
                actual_entry
                - actual_sl
            )

            state["positions"][pid] = {
                "risk": actual_risk,
                "entry": actual_entry,
                "side": side
            }

            state["trade_count"] += 1
            state["last_trade_time"] = (
                time.time()
            )

            previous_ids.add(pid)

            save_state(state)

            notify(
                f"RIO V12 ORDER {number}/4 OK\n"
                f"SIDE: {side}\n"
                f"ENTRY: {actual_entry}\n"
                f"SL: {actual_sl}\n"
                f"TP: {actual_tp}"
            )

        state["order_uncertain"] = False
        save_state(state)

        notify(
            "RIO V12 BATCH COMPLETED\n"
            f"CONFIRMED: "
            f"{state['trade_count']}/4\n"
            "BE ONLY ACTIVE"
        )

    except Exception as e:

        state["halted"] = True
        state["order_uncertain"] = True

        save_state(state)

        notify(
            "RIO V12 BATCH STOPPED\n"
            f"ERROR: {type(e).__name__}\n"
            f"{str(e)[:100]}"
        )


# =====================================================
# BREAK EVEN
# =====================================================

async def protect_position(
    connection,
    position,
    state,
    market
):

    pid = str(
        position["id"]
    )

    info = state[
        "positions"
    ].get(pid)

    if not info:
        return

    side = position_side(
        position
    )

    if side is None:
        return

    risk = float(
        info["risk"]
    )

    entry = float(
        position["openPrice"]
    )

    current_sl = float(
        position.get("stopLoss")
        or 0
    )

    current_tp = position.get(
        "takeProfit"
    )

    if (
        risk <= 0
        or current_sl <= 0
    ):
        return

    direction = (
        1
        if side == "BUY"
        else -1
    )

    price = (
        market["bid"]
        if side == "BUY"
        else market["ask"]
    )

    profit_distance = (
        price - entry
    ) * direction

    if (
        profit_distance
        < risk * BE_TRIGGER_RR
    ):
        return

    wanted_sl = (
        entry
        + direction
        * risk
        * BE_LOCK_RR
    )

    wanted_sl = normalize(
        wanted_sl,
        market
    )

    tick = market["tick"]

    if side == "BUY":

        if (
            wanted_sl
            <= current_sl + tick / 2
        ):
            return

    else:

        if (
            wanted_sl
            >= current_sl - tick / 2
        ):
            return

    spec = market["spec"]

    point = float(
        spec.get("point")
        or 10 ** (-market["digits"])
    )

    stops = float(
        spec.get("stopsLevel")
        or 0
    )

    freeze = float(
        spec.get("freezeLevel")
        or 0
    )

    min_distance = (
        max(
            stops,
            freeze
        )
        * point
        + tick
    )

    if side == "BUY":

        if (
            wanted_sl
            >= market["bid"]
            - min_distance
        ):
            return

    else:

        if (
            wanted_sl
            <= market["ask"]
            + min_distance
        ):
            return

    await meta_call(
        connection.modify_position(
            position["id"],
            wanted_sl,
            current_tp
        ),
        timeout=20
    )

    notify(
        "RIO V12 BREAK EVEN ACTIVE\n"
        f"POSITION: {pid}\n"
        f"SIDE: {side}\n"
        f"BE SL: {wanted_sl}"
    )


# =====================================================
# RECOVER POSITIONS
# =====================================================

async def adopt_positions(
    connection,
    state
):

    positions = await get_positions(
        connection
    )

    if not positions:
        return

    adopted = 0

    for position in positions:

        pid = str(
            position["id"]
        )

        if pid in state["positions"]:
            continue

        side = position_side(
            position
        )

        if side is None:
            continue

        entry = float(
            position.get("openPrice")
            or 0
        )

        sl = float(
            position.get("stopLoss")
            or 0
        )

        if (
            entry <= 0
            or sl <= 0
        ):
            continue

        risk = abs(
            entry - sl
        )

        if risk <= 0:
            continue

        state["positions"][pid] = {
            "risk": risk,
            "entry": entry,
            "side": side
        }

        adopted += 1

    if adopted:

        state["trade_count"] = len(
            state["positions"]
        )

        save_state(state)

        notify(
            "RIO V12 POSITIONS ADOPTED\n"
            f"OPEN: {len(positions)}/4\n"
            f"TRACKED: "
            f"{len(state['positions'])}/4\n"
            "BE PROTECTION RESTORED"
        )


# =====================================================
# CONNECTION SESSION
# =====================================================

async def bot_session(state):

    api = MetaApi(
        M_TOKEN
    )

    account = await meta_call(
        api.metatrader_account_api.get_account(
            M_ACC
        ),
        timeout=35
    )

    region = (
        getattr(
            account,
            "region",
            None
        )
        or "london"
    )

    if (
        str(account.state).upper()
        != "DEPLOYED"
    ):

        await meta_call(
            account.deploy(),
            timeout=45
        )

    await asyncio.wait_for(
        account.wait_connected(),
        timeout=CONNECT_TIMEOUT
    )

    connection = (
        account.get_rpc_connection()
    )

    try:

        await meta_call(
            connection.connect(),
            timeout=CONNECT_TIMEOUT
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=CONNECT_TIMEOUT
        )

        await adopt_positions(
            connection,
            state
        )

        positions = await get_positions(
            connection
        )

        if (
            not positions
            and not state["order_uncertain"]
        ):

            state["positions"] = {}
            state["trade_count"] = 0
            state["halted"] = False

            save_state(state)

        notify(
            "RIO GOLD V12 SWING ZONES CONNECTED\n"
            "POSITIONS VERIFIED\n"
            f"OPEN: {len(positions)}/4\n"
            f"COUNT: "
            f"{state['trade_count']}/4\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        while True:

            try:

                positions = await get_positions(
                    connection
                )

                current_ids = {
                    str(p["id"])
                    for p in positions
                }

                known_ids = set(
                    state["positions"].keys()
                )

                closed_ids = (
                    known_ids - current_ids
                )

                for pid in closed_ids:

                    del state[
                        "positions"
                    ][pid]

                    state[
                        "last_trade_time"
                    ] = time.time()

                    save_state(state)

                    notify(
                        "RIO V12 POSITION CLOSED\n"
                        f"ID: {pid}\n"
                        f"REMAINING: "
                        f"{len(state['positions'])}"
                    )

                if (
                    not positions
                    and state["trade_count"] > 0
                    and not state["order_uncertain"]
                ):

                    state["trade_count"] = 0
                    state["positions"] = {}
                    state["halted"] = False
                    state["last_trade_time"] = (
                        time.time()
                    )

                    save_state(state)

                    notify(
                        "RIO V12 BATCH FINISHED\n"
                        "COUNT RESET: 0/4\n"
                        "WAITING FOR NEW SIGNAL"
                    )

                if positions:

                    market = await get_market(
                        connection
                    )

                    for position in positions:

                        pid = str(
                            position["id"]
                        )

                        if (
                            pid
                            not in state[
                                "positions"
                            ]
                        ):
                            continue

                        await protect_position(
                            connection,
                            position,
                            state,
                            market
                        )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                if (
                    state["halted"]
                    or state["order_uncertain"]
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                if (
                    time.time()
                    - state["last_trade_time"]
                    < COOLDOWN_SECONDS
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                if broker_break_blocked():

                    print(
                        "ENTRY BLOCKED: "
                        "BROKER BREAK SOON",
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                if await news_blocked():

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                try:

                    candles = await get_candles(
                        region
                    )

                except RuntimeError as e:

                    if str(e) == "STALE M1 DATA":

                        print(
                            "M1 DATA STALE - "
                            "WAITING",
                            flush=True
                        )

                        await asyncio.sleep(
                            LOOP_SECONDS
                        )
                        continue

                    raise

                candle_time = (
                    candles[-1]["time"]
                )

                if (
                    candle_time
                    == state["last_candle"]
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                atr = calculate_atr(
                    candles
                )

                if (
                    atr is None
                    or atr <= 0
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                (
                    support,
                    resistance,
                    tolerance
                ) = detect_zones(
                    candles,
                    atr
                )

                print(
                    "LOCAL ZONES:",
                    f"SUPPORT={support:.2f}",
                    f"RESISTANCE={resistance:.2f}",
                    f"ATR={atr:.2f}",
                    flush=True
                )

                (
                    side,
                    zone,
                    reason
                ) = get_signal(
                    candles,
                    support,
                    resistance,
                    tolerance,
                    atr
                )

                state[
                    "last_candle"
                ] = candle_time

                save_state(state)

                if side is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                (
                    trend_ok,
                    m5_close,
                    ema50
                ) = m5_trend_filter(
                    candles,
                    side
                )

                if not trend_ok:

                    print(
                        "SIGNAL BLOCKED BY M5 EMA50",
                        side,
                        "M5:",
                        m5_close,
                        "EMA50:",
                        ema50,
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                if not momentum_filter(
                    candles,
                    side
                ):

                    print(
                        "SIGNAL BLOCKED BY "
                        "M1 MOMENTUM:",
                        side,
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )
                    continue

                signal_key = (
                    f"{candle_time}:{side}"
                )

                if (
                    signal_key
                    != state["last_signal"]
                ):

                    state[
                        "last_signal"
                    ] = signal_key

                    save_state(state)

                    notify(
                        "RIO V12 SIGNAL APPROVED\n"
                        f"SIDE: {side}\n"
                        f"LOCAL ZONE: {zone:.2f}\n"
                        f"ATR: {atr:.2f}\n"
                        f"M5 EMA50: {ema50:.2f}\n"
                        "STRICT ZONE REJECTION: OK\n"
                        "M1 MOMENTUM: OK\n"
                        "REQUEST: 4 POSITIONS"
                    )

                    await open_batch(
                        connection,
                        side,
                        zone,
                        atr,
                        candles[-1]["close"],
                        state
                    )

                await asyncio.sleep(
                    LOOP_SECONDS
                )

            except Exception as e:

                print(
                    "LOOP ERROR:",
                    traceback.format_exc(),
                    flush=True
                )

                message = str(
                    e
                ).lower()

                connection_error = (
                    isinstance(
                        e,
                        (
                            asyncio.TimeoutError,
                            TimeoutError,
                            ConnectionError
                        )
                    )
                    or any(
                        word in message
                        for word in (
                            "metaapi positions failed",
                            "not connected",
                            "not synchronized",
                            "websocket",
                            "timed out",
                            "timeout",
                            "subscription",
                            "disconnected",
                            "broker yet",
                            "connection lost"
                        )
                    )
                )

                if connection_error:

                    raise RuntimeError(
                        "METAAPI CONNECTION LOST "
                        "AFTER RETRIES"
                    ) from e

                await asyncio.sleep(
                    LOOP_SECONDS
                )

    finally:

        try:

            await asyncio.wait_for(
                connection.close(),
                timeout=10
            )

        except Exception:
            pass


# =====================================================
# MAIN
# =====================================================

async def main():

    keep_alive()

    if not M_TOKEN or not M_ACC:

        telegram(
            "RIOBOT ERROR\n"
            "M_TOKEN OR M_ACC MISSING"
        )
        return

    state = load_state()

    telegram(
        "RIOBOT GOLD V12 SWING ZONES START\n"
        "STRICT CURRENT-CANDLE ZONE ENTRY\n"
        "M1 LOCAL SWING SUPPORT/RESISTANCE\n"
        "ZONE REJECTION CONFIRMATION\n"
        "OPPOSITE ZONE PROTECTION\n"
        "ATR14\n"
        "M5 EMA50 TREND FILTER\n"
        "M1 MOMENTUM FILTER\n"
        "METAAPI 3 RETRIES ACTIVE\n"
        "AUTO BATCH RESET ACTIVE\n"
        "4 POSITIONS ON ONE SIGNAL\n"
        "ATR SL + 1.5R TP\n"
        "BE ONLY\n"
        "RECONNECT POSITION RECOVERY\n"
        f"LOT EACH: {LOT_SIZE}\n"
        f"BATCH: {BATCH_SIZE}\n"
        f"COUNT: {state['trade_count']}/4\n"
        f"LIVE: {ENABLE_TRADING}\n"
        f"SAFETY HALT: {state['halted']}"
    )

    while True:

        try:

            await bot_session(
                state
            )

        except Exception as e:

            print(
                "SESSION ERROR:",
                traceback.format_exc(),
                flush=True
            )

            notify(
                "RIO V12 CONNECTION ERROR\n"
                f"{type(e).__name__}: "
                f"{str(e)[:100]}\n"
                "RECONNECT IN 5 SECONDS"
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


# =====================================================
# START
# =====================================================

if __name__ == "__main__":

    asyncio.run(
        main()
                   )
