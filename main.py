import os
import json
import time
import asyncio
import requests
import traceback

from datetime import datetime, timezone
from threading import Thread

from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =====================================================
# RIOBOT GOLD V6
# FAST M1 + M5 / ATR SL / EARLY BE
# METAAPI DIAGNOSTICS + RECONNECT
# =====================================================

SYMBOL = "XAUUSD"
COMMENT = "RIO GOLD V6"

LOT_SIZE = 0.01
MAX_TRADES = 4

ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "false")
    .strip().lower() == "true"
)

LOOP_SECONDS = 3
RECONNECT_SECONDS = 5
META_TIMEOUT = 60
SYNC_TIMEOUT = 180

COOLDOWN_SECONDS = 180

ATR_PERIOD = 14
ZONE_LOOKBACK = 35
M5_EMA_PERIOD = 5

BREAKOUT_LOOKBACK = 3
MIN_BREAKOUT_BODY_RATIO = 0.45
MIN_BODY_RATIO = 0.30

MAX_SIGNAL_RANGE_ATR = 2.00
MAX_ENTRY_DRIFT_ATR = 0.30

ZONE_ATR_TOLERANCE = 0.35
MIN_ZONE_TOLERANCE = 0.35

# ATR SL
SL_ATR_MULTIPLIER = 1.35
MIN_SL_DISTANCE = 1.00
MAX_SL_DISTANCE = 12.00

MAX_SPREAD = 0.40

# BE
BE_TRIGGER_RR = 0.45
BE_LOCK_DISTANCE = 0.10

# Virtual TP1
TP1_RR = 0.85
TP1_LOCK_RR = 0.40

# Trailing
TRAIL_TRIGGER_RR = 1.20
TRAIL_ATR_MULTIPLIER = 0.85

NEWS_BEFORE = 15
NEWS_AFTER = 30
NEWS_REFRESH = 1800
NEWS_MAX_AGE = 10800

NEWS_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

STATE_DIR = os.getenv("RIO_STATE_DIR", "/tmp")

# Zachovame nazov V5, aby sa pri prechode
# na V6 zbytocne nevynuloval stav v tom istom
# bezacom filesysteme.
STATE_FILE = os.path.join(
    STATE_DIR,
    "rio_gold_v5.json"
)

news_cache = {
    "events": [],
    "updated": 0
}


# =====================================================
# RENDER
# =====================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIOBOT GOLD V6 ACTIVE", 200


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
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=8
        )

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            str(e),
            flush=True
        )


def notify(message):

    asyncio.create_task(
        asyncio.to_thread(telegram, message)
    )


# =====================================================
# STATE
# =====================================================

def default_state():

    return {
        "trade_count": 0,
        "position_id": None,
        "trade_risk": None,
        "last_trade_time": 0,
        "last_candle": None,
        "last_signal": None,
        "had_position": False,
        "tp1_reached": False,
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

    os.replace(temp, STATE_FILE)


def load_state():

    state = default_state()

    if not os.path.exists(STATE_FILE):

        print(
            "WARNING: STATE FILE MISSING. "
            "RENDER /tmp IS NOT PERSISTENT.",
            flush=True
        )

        # Bez perzistentneho stavu nevieme
        # overit pocet obchodov po redeployi.
        # Pri LIVE preto nove vstupy zastavime.
        if ENABLE_TRADING:
            state["halted"] = True

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
            str(e),
            flush=True
        )

        state["halted"] = True

    return state


# =====================================================
# METAAPI - PRESNA DIAGNOSTIKA
# =====================================================

class MetaConnectionError(Exception):
    pass


async def meta_call(name, coroutine, timeout=None):

    timeout = timeout or META_TIMEOUT

    try:

        return await asyncio.wait_for(
            coroutine,
            timeout=timeout
        )

    except asyncio.TimeoutError as e:

        message = (
            f"METAAPI TIMEOUT: {name} "
            f"AFTER {timeout}s"
        )

        print(message, flush=True)

        raise MetaConnectionError(message) from e

    except Exception as e:

        message = (
            f"METAAPI ERROR [{name}]: "
            f"{type(e).__name__}: {str(e)[:200]}"
        )

        print(message, flush=True)

        raise MetaConnectionError(message) from e


# =====================================================
# M1 CANDLES
# =====================================================

async def get_candles(region):

    url = (
        "https://mt-market-data-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/"
        "historical-market-data/symbols/"
        f"{SYMBOL}/timeframes/1m/candles"
        "?limit=180"
    )

    def fetch():

        response = requests.get(
            url,
            headers={"auth-token": M_TOKEN},
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    try:

        raw = await asyncio.wait_for(
            asyncio.to_thread(fetch),
            timeout=20
        )

    except Exception as e:

        raise RuntimeError(
            f"M1 HISTORY ERROR: {type(e).__name__}: {e}"
        ) from e

    if not isinstance(raw, list):

        raise RuntimeError(
            "INVALID CANDLES RESPONSE"
        )

    now = datetime.now(timezone.utc)

    candles = []

    for item in raw:

        try:

            dt = datetime.fromisoformat(
                str(item["time"]).replace(
                    "Z", "+00:00"
                )
            )

            if dt.tzinfo is None:

                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            dt = dt.astimezone(timezone.utc)

            age = (
                now - dt
            ).total_seconds()

            # Len uzavrete M1.
            if age < 62:
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

    unique = {
        c["time"]: c
        for c in candles
    }

    candles = sorted(
        unique.values(),
        key=lambda c: c["time"]
    )

    if len(candles) < 70:

        raise RuntimeError(
            "NOT ENOUGH M1 HISTORY"
        )

    last_time = datetime.fromisoformat(
        candles[-1]["time"]
    )

    if (
        now - last_time
    ).total_seconds() > 180:

        raise RuntimeError(
            "STALE M1 DATA"
        )

    return candles


# =====================================================
# ATR
# =====================================================

def calculate_atr(candles):

    if len(candles) < ATR_PERIOD + 2:
        return None

    ranges = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(
                current["high"] - previous["close"]
            ),
            abs(
                current["low"] - previous["close"]
            )
        )

        ranges.append(tr)

    return (
        sum(ranges[-ATR_PERIOD:])
        / ATR_PERIOD
    )


# =====================================================
# M5 FROM M1
# =====================================================

def build_m5(candles):

    groups = {}

    for candle in candles:

        dt = datetime.fromisoformat(
            candle["time"]
        )

        timestamp = int(
            dt.timestamp()
        )

        bucket = (
            timestamp - timestamp % 300
        )

        groups.setdefault(
            bucket,
            []
        ).append(candle)

    result = []

    for bucket in sorted(groups):

        group = sorted(
            groups[bucket],
            key=lambda c: c["time"]
        )

        if len(group) != 5:
            continue

        minutes = [
            int(
                datetime.fromisoformat(
                    c["time"]
                ).timestamp()
            )
            for c in group
        ]

        expected = [
            bucket + i * 60
            for i in range(5)
        ]

        if minutes != expected:
            continue

        result.append({
            "time": datetime.fromtimestamp(
                bucket,
                timezone.utc
            ).isoformat(),

            "open": group[0]["open"],

            "high": max(
                c["high"] for c in group
            ),

            "low": min(
                c["low"] for c in group
            ),

            "close": group[-1]["close"]
        })

    return result


def ema_values(values, period):

    if len(values) < period + 2:
        return []

    multiplier = 2 / (period + 1)

    ema = (
        sum(values[:period]) / period
    )

    result = [ema]

    for value in values[period:]:

        ema = (
            value * multiplier
            + ema * (1 - multiplier)
        )

        result.append(ema)

    return result


def m5_trend(candles):

    m5 = build_m5(candles)

    if len(m5) < M5_EMA_PERIOD + 3:

        return (
            "NONE",
            "NOT ENOUGH M5 HISTORY"
        )

    closes = [
        c["close"] for c in m5
    ]

    emas = ema_values(
        closes,
        M5_EMA_PERIOD
    )

    if len(emas) < 3:

        return "NONE", "EMA NOT READY"

    last = m5[-1]
    previous = m5[-2]

    ema_now = emas[-1]
    ema_previous = emas[-2]

    bullish = (
        last["close"] > ema_now
        and ema_now > ema_previous
        and last["close"] > previous["close"]
    )

    bearish = (
        last["close"] < ema_now
        and ema_now < ema_previous
        and last["close"] < previous["close"]
    )

    if bullish:

        return "BUY", "M5 UPTREND"

    if bearish:

        return "SELL", "M5 DOWNTREND"

    return (
        "NONE",
        "M5 TREND NOT CONFIRMED"
    )


# =====================================================
# SUPPORT / RESISTANCE
# =====================================================

def detect_zones(candles, atr):

    history = candles[
        -(ZONE_LOOKBACK + 3):-3
    ]

    if len(history) < ZONE_LOOKBACK:

        return None, None, None

    support = min(
        c["low"] for c in history
    )

    resistance = max(
        c["high"] for c in history
    )

    tolerance = max(
        MIN_ZONE_TOLERANCE,
        atr * ZONE_ATR_TOLERANCE
    )

    return support, resistance, tolerance


# =====================================================
# FAST SIGNAL
# =====================================================

def get_signal(
    candles,
    support,
    resistance,
    tolerance,
    atr
):

    if len(candles) < 10:

        return None, None, "M1 HISTORY SHORT"

    a = candles[-3]
    b = candles[-2]
    c = candles[-1]

    candle_range = (
        c["high"] - c["low"]
    )

    if candle_range <= 0:

        return None, None, "ZERO RANGE"

    if (
        candle_range
        > atr * MAX_SIGNAL_RANGE_ATR
    ):

        return None, None, "IMPULSE FILTER"

    body_ratio = (
        abs(c["close"] - c["open"])
        / candle_range
    )

    trend, trend_reason = m5_trend(
        candles
    )

    if trend == "NONE":

        return None, None, trend_reason

    # FAST BREAKOUT

    previous = candles[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    previous_high = max(
        x["high"] for x in previous
    )

    previous_low = min(
        x["low"] for x in previous
    )

    fast_buy = (
        trend == "BUY"
        and c["close"] > c["open"]
        and body_ratio >= MIN_BREAKOUT_BODY_RATIO
        and c["close"] > previous_high
        and c["close"] - previous_high
        <= atr * 0.35
    )

    fast_sell = (
        trend == "SELL"
        and c["close"] < c["open"]
        and body_ratio >= MIN_BREAKOUT_BODY_RATIO
        and c["close"] < previous_low
        and previous_low - c["close"]
        <= atr * 0.35
    )

    if fast_buy:

        return (
            "BUY",
            previous_low,
            "FAST M1 BREAKOUT + M5 BUY"
        )

    if fast_sell:

        return (
            "SELL",
            previous_high,
            "FAST M1 BREAKDOWN + M5 SELL"
        )

    # ZONE REJECTION

    if (
        support is None
        or resistance is None
    ):

        return None, None, "WAITING"

    buy_touch = (
        a["low"] <= support + tolerance
        and a["high"] >= support - tolerance
    )

    sell_touch = (
        a["high"] >= resistance - tolerance
        and a["low"] <= resistance + tolerance
    )

    buy_confirmed = (
        trend == "BUY"
        and b["close"] > b["open"]
        and c["close"] > c["open"]
        and c["close"] > b["high"]
        and body_ratio >= MIN_BODY_RATIO
    )

    sell_confirmed = (
        trend == "SELL"
        and b["close"] < b["open"]
        and c["close"] < c["open"]
        and c["close"] < b["low"]
        and body_ratio >= MIN_BODY_RATIO
    )

    if buy_touch and buy_confirmed:

        if c["close"] - support <= atr * 1.8:

            return (
                "BUY",
                support,
                "SUPPORT REJECTION + M5 BUY"
            )

    if sell_touch and sell_confirmed:

        if resistance - c["close"] <= atr * 1.8:

            return (
                "SELL",
                resistance,
                "RESISTANCE REJECTION + M5 SELL"
            )

    return None, None, "WAITING"


# =====================================================
# NEWS FILTER
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
            "INVALID NEWS DATA"
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
                str(event["date"]).replace(
                    "Z", "+00:00"
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

    if (
        now - news_cache["updated"]
        >= NEWS_REFRESH
    ):

        try:

            events = await asyncio.wait_for(
                asyncio.to_thread(fetch_news),
                timeout=16
            )

            news_cache["events"] = events
            news_cache["updated"] = time.time()

        except Exception as e:

            print(
                "NEWS ERROR:",
                str(e),
                flush=True
            )

    if (
        now - news_cache["updated"]
        > NEWS_MAX_AGE
    ):

        return True

    for event_time in news_cache["events"]:

        if (
            event_time - NEWS_BEFORE * 60
            <= now
            <= event_time + NEWS_AFTER * 60
        ):

            return True

    return False


# =====================================================
# MARKET
# =====================================================

async def get_market(connection):

    spec = await meta_call(
        "GET SYMBOL SPECIFICATION",
        connection.get_symbol_specification(
            SYMBOL
        )
    )

    price = await meta_call(
        "GET SYMBOL PRICE",
        connection.get_symbol_price(
            SYMBOL
        )
    )

    if not spec or not price:

        raise RuntimeError(
            "MARKET DATA NOT READY"
        )

    digits = int(
        spec.get("digits", 2)
    )

    tick = float(
        spec.get("tickSize")
        or 10 ** (-digits)
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

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
        round(value / tick) * tick,
        market["digits"]
    )


async def get_positions(connection):

    positions = await meta_call(
        "GET POSITIONS",
        connection.get_positions()
    )

    if not isinstance(positions, list):

        raise RuntimeError(
            "INVALID POSITION DATA"
        )

    return [
        p for p in positions
        if str(
            p.get("symbol", "")
        ).upper() == SYMBOL
    ]


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
# ATR SL
# =====================================================

def get_levels(
    side,
    entry,
    zone,
    atr,
    market
):

    distance = max(
        MIN_SL_DISTANCE,
        atr * SL_ATR_MULTIPLIER
    )

    # Ak je volatilita privelka,
    # obchod sa vynecha.
    if distance > MAX_SL_DISTANCE:

        return None, (
            f"ATR SL TOO LARGE: {distance:.2f}"
        )

    if side == "BUY":

        sl = normalize(
            entry - distance,
            market
        )

    else:

        sl = normalize(
            entry + distance,
            market
        )

    risk = abs(entry - sl)

    if (
        risk < MIN_SL_DISTANCE
        or risk > MAX_SL_DISTANCE
    ):

        return None, (
            f"INVALID SL DISTANCE: {risk:.2f}"
        )

    direction = (
        1 if side == "BUY" else -1
    )

    tp1 = normalize(
        entry + direction * risk * TP1_RR,
        market
    )

    return {
        "sl": sl,
        "risk": risk,
        "tp1": tp1
    }, None


# =====================================================
# ORDER
# =====================================================

async def open_trade(
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
        or state["trade_count"] >= MAX_TRADES
    ):

        return False

    positions = await get_positions(
        connection
    )

    if positions:

        print(
            "ENTRY BLOCKED: POSITION EXISTS",
            flush=True
        )

        return False

    market = await get_market(
        connection
    )

    spread = (
        market["ask"] - market["bid"]
    )

    if spread > MAX_SPREAD:

        print(
            f"ENTRY BLOCKED: SPREAD {spread:.2f}",
            flush=True
        )

        return False

    spec = market["spec"]

    minimum_volume = float(
        spec.get("minVolume") or 0.01
    )

    volume_step = float(
        spec.get("volumeStep") or 0.01
    )

    if LOT_SIZE < minimum_volume:

        notify(
            f"ENTRY BLOCKED: MIN LOT {minimum_volume}"
        )

        return False

    if volume_step <= 0:
        return False

    if abs(
        LOT_SIZE / volume_step
        - round(LOT_SIZE / volume_step)
    ) > 1e-7:

        notify(
            "ENTRY BLOCKED: INVALID LOT STEP"
        )

        return False

    entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )

    if (
        abs(entry - signal_close)
        > atr * MAX_ENTRY_DRIFT_ATR
    ):

        print(
            "ENTRY BLOCKED: PRICE MOVED",
            flush=True
        )

        return False

    levels, reason = get_levels(
        side,
        entry,
        zone,
        atr,
        market
    )

    if levels is None:

        notify(
            f"{side} SIGNAL BLOCKED\n{reason}"
        )

        return False

    sl = levels["sl"]

    point = float(
        spec.get("point")
        or 10 ** (-market["digits"])
    )

    stops = float(
        spec.get("stopsLevel") or 0
    )

    minimum_distance = (
        stops * point
        + market["tick"]
    )

    if side == "BUY":

        if (
            market["bid"] - sl
            <= minimum_distance
        ):

            print(
                "BUY STOP DISTANCE BLOCKED",
                flush=True
            )

            return False

    else:

        if (
            sl - market["ask"]
            <= minimum_distance
        ):

            print(
                "SELL STOP DISTANCE BLOCKED",
                flush=True
            )

            return False

    message = (
        "RIO GOLD V6\n"
        f"SIDE: {side}\n"
        f"LOT: {LOT_SIZE}\n"
        f"ENTRY: {entry}\n"
        f"SL: {sl}\n"
        f"ATR: {atr:.2f}\n"
        f"RISK DISTANCE: {levels['risk']:.2f}\n"
        f"TP1 LEVEL: {levels['tp1']}\n"
        f"TRADE: {state['trade_count'] + 1}/{MAX_TRADES}"
    )

    if not ENABLE_TRADING:

        notify(
            "TEST SIGNAL - NO REAL ORDER\n"
            + message
        )

        return False

    # Pred objednavkou ulozime neistotu.
    # Po timeoute nikdy neposleme druhy
    # obchod automaticky.
    state["order_uncertain"] = True

    save_state(state)

    try:

        options = {
            "comment": COMMENT
        }

        if side == "BUY":

            result = await meta_call(
                "CREATE BUY ORDER",
                connection.create_market_buy_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    None,
                    options
                ),
                timeout=60
            )

        else:

            result = await meta_call(
                "CREATE SELL ORDER",
                connection.create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    None,
                    options
                ),
                timeout=60
            )

        print(
            "ORDER RESPONSE:",
            result,
            flush=True
        )

        await asyncio.sleep(2)

        positions = await get_positions(
            connection
        )

        matches = [
            p for p in positions
            if (
                position_side(p) == side
                and abs(
                    float(p.get("volume", 0))
                    - LOT_SIZE
                ) < 0.000001
            )
        ]

        if (
            len(positions) != 1
            or len(matches) != 1
        ):

            state["halted"] = True

            save_state(state)

            notify(
                "ORDER VERIFICATION FAILED\n"
                "CHECK MT5\n"
                "NEW ENTRIES STOPPED"
            )

            return False

        position = matches[0]

        actual_sl = float(
            position.get("stopLoss") or 0
        )

        if actual_sl <= 0:

            state["halted"] = True

            save_state(state)

            notify(
                "CRITICAL: ORDER WITHOUT SL\n"
                "CHECK MT5 IMMEDIATELY"
            )

            return False

        actual_entry = float(
            position.get("openPrice") or entry
        )

        actual_risk = abs(
            actual_entry - actual_sl
        )

        state["trade_count"] += 1

        state["position_id"] = str(
            position["id"]
        )

        state["trade_risk"] = actual_risk

        state["had_position"] = True
        state["tp1_reached"] = False

        state["order_uncertain"] = False

        state["last_trade_time"] = time.time()

        if state["trade_count"] >= MAX_TRADES:

            state["halted"] = True

        save_state(state)

        notify(
            "ORDER OK\n"
            + message
            + f"\nACTUAL ENTRY: {actual_entry}"
            + f"\nACTUAL SL: {actual_sl}"
        )

        return True

    except Exception as e:

        state["halted"] = True

        save_state(state)

        notify(
            "ORDER STATUS UNCERTAIN\n"
            f"{type(e).__name__}: {str(e)[:180]}\n"
            "CHECK MT5\n"
            "NEW ENTRIES STOPPED"
        )

        return False


# =====================================================
# BE / TP1 / TRAILING
# =====================================================

async def protect_position(
    connection,
    position,
    state,
    atr
):

    side = position_side(position)

    if side is None:
        return

    if (
        str(position["id"])
        != str(state["position_id"])
    ):

        return

    risk = state["trade_risk"]

    if not risk or risk <= 0:
        return

    market = await get_market(
        connection
    )

    position_id = position["id"]

    entry = float(
        position["openPrice"]
    )

    current_sl = float(
        position.get("stopLoss") or 0
    )

    if current_sl <= 0:

        state["halted"] = True

        save_state(state)

        notify(
            "CRITICAL: POSITION WITHOUT SL\n"
            "CHECK MT5 IMMEDIATELY"
        )

        return

    direction = (
        1 if side == "BUY" else -1
    )

    price = (
        market["bid"]
        if side == "BUY"
        else market["ask"]
    )

    profit_distance = (
        price - entry
    ) * direction

    wanted_sl = current_sl
    stage = None

    # EARLY BE +0.45R

    if (
        profit_distance
        >= risk * BE_TRIGGER_RR
    ):

        be = (
            entry
            + direction * BE_LOCK_DISTANCE
        )

        if side == "BUY":

            wanted_sl = max(
                wanted_sl,
                be
            )

        else:

            wanted_sl = min(
                wanted_sl,
                be
            )

        stage = "EARLY BE"

    # TP1 +0.85R

    if (
        profit_distance
        >= risk * TP1_RR
    ):

        lock = (
            entry
            + direction * risk * TP1_LOCK_RR
        )

        if side == "BUY":

            wanted_sl = max(
                wanted_sl,
                lock
            )

        else:

            wanted_sl = min(
                wanted_sl,
                lock
            )

        stage = "TP1 LOCK"

    # TRAILING +1.20R

    if (
        profit_distance
        >= risk * TRAIL_TRIGGER_RR
        and atr is not None
        and atr > 0
    ):

        trail = (
            price
            - direction
            * atr
            * TRAIL_ATR_MULTIPLIER
        )

        if side == "BUY":

            wanted_sl = max(
                wanted_sl,
                trail
            )

        else:

            wanted_sl = min(
                wanted_sl,
                trail
            )

        stage = "TRAILING"

    wanted_sl = normalize(
        wanted_sl,
        market
    )

    tick = market["tick"]

    # SL nikdy neposuvat spat.

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
        spec.get("stopsLevel") or 0
    )

    freeze = float(
        spec.get("freezeLevel") or 0
    )

    minimum_distance = (
        max(stops, freeze) * point
        + tick
    )

    if side == "BUY":

        if (
            wanted_sl
            >= market["bid"] - minimum_distance
        ):

            return

    else:

        if (
            wanted_sl
            <= market["ask"] + minimum_distance
        ):

            return

    await meta_call(
        "MODIFY POSITION SL",
        connection.modify_position(
            position_id,
            wanted_sl,
            position.get("takeProfit")
        ),
        timeout=60
    )

    notify(
        "RIO SL UPDATED\n"
        f"SIDE: {side}\n"
        f"STAGE: {stage}\n"
        f"SL: {wanted_sl}"
    )

    if (
        stage in ("TP1 LOCK", "TRAILING")
        and not state["tp1_reached"]
    ):

        state["tp1_reached"] = True

        save_state(state)

        notify(
            "TP1 REACHED\n"
            "POSITION REMAINS OPEN"
        )


# =====================================================
# METAAPI SESSION
# =====================================================

async def bot_session(state):

    # SDK region nenastavujeme manualne.
    api = MetaApi(M_TOKEN)

    account = await meta_call(
        "GET ACCOUNT",
        api.metatrader_account_api.get_account(
            M_ACC
        )
    )

    region = (
        getattr(account, "region", None)
        or "london"
    )

    print(
        "METAAPI REGION:",
        region,
        flush=True
    )

    if (
        str(account.state).upper()
        != "DEPLOYED"
    ):

        await meta_call(
            "DEPLOY ACCOUNT",
            account.deploy()
        )

    await meta_call(
        "WAIT BROKER CONNECTED",
        account.wait_connected(),
        timeout=SYNC_TIMEOUT
    )

    connection = account.get_rpc_connection()

    try:

        await meta_call(
            "RPC CONNECT",
            connection.connect(),
            timeout=SYNC_TIMEOUT
        )

        await meta_call(
            "RPC SYNCHRONIZE",
            connection.wait_synchronized(),
            timeout=SYNC_TIMEOUT
        )

        positions = await get_positions(
            connection
        )

        notify(
            "RIO GOLD V6 CONNECTED\n"
            f"LOT: {LOT_SIZE}\n"
            f"TRADES: {state['trade_count']}/{MAX_TRADES}\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        if positions:

            state["had_position"] = True

            if (
                len(positions) != 1
                or state["position_id"] is None
                or str(positions[0]["id"])
                != str(state["position_id"])
            ):

                state["halted"] = True

                save_state(state)

                notify(
                    "UNKNOWN OPEN POSITION\n"
                    "NEW ENTRIES STOPPED\n"
                    "CHECK MT5"
                )

        if state["order_uncertain"]:

            state["halted"] = True

            save_state(state)

            notify(
                "PREVIOUS ORDER UNCERTAIN\n"
                "NEW ENTRIES STOPPED"
            )

        if state["halted"]:

            notify(
                "SAFETY HALT ACTIVE\n"
                "POSITION MONITORING CONTINUES\n"
                "NEW ENTRIES DISABLED"
            )

        last_atr = None
        last_atr_time = 0

        while True:

            try:

                # =================================
                # 1. OTVORENE POZICIE
                # =================================

                positions = await get_positions(
                    connection
                )

                if positions:

                    state["had_position"] = True

                    if (
                        len(positions) != 1
                        or state["position_id"] is None
                        or str(positions[0]["id"])
                        != str(state["position_id"])
                    ):

                        if not state["halted"]:

                            state["halted"] = True

                            save_state(state)

                            notify(
                                "POSITION MISMATCH\n"
                                "NEW ENTRIES STOPPED"
                            )

                    if (
                        time.time() - last_atr_time
                        >= 60
                    ):

                        try:

                            candles = await get_candles(
                                region
                            )

                            last_atr = calculate_atr(
                                candles
                            )

                            last_atr_time = time.time()

                        except Exception as e:

                            print(
                                "ATR REFRESH ERROR:",
                                str(e),
                                flush=True
                            )

                    for position in positions:

                        if (
                            state["position_id"] is not None
                            and str(position["id"])
                            == str(state["position_id"])
                        ):

                            await protect_position(
                                connection,
                                position,
                                state,
                                last_atr
                            )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================
                # 2. POZICIA SA ZATVORILA
                # =================================

                if state["had_position"]:

                    state["had_position"] = False
                    state["position_id"] = None
                    state["trade_risk"] = None
                    state["tp1_reached"] = False

                    state["last_trade_time"] = (
                        time.time()
                    )

                    save_state(state)

                    notify(
                        "POSITION CLOSED\n"
                        "COOLDOWN 180 SECONDS"
                    )

                # =================================
                # 3. SAFETY
                # =================================

                if (
                    state["halted"]
                    or state["order_uncertain"]
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                if (
                    state["trade_count"]
                    >= MAX_TRADES
                ):

                    state["halted"] = True

                    save_state(state)

                    notify(
                        "MAX 4 TRADES REACHED\n"
                        "NEW ENTRIES STOPPED"
                    )

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

                # =================================
                # 4. NEWS
                # =================================

                if await news_blocked():

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================
                # 5. SIGNAL
                # =================================

                candles = await get_candles(
                    region
                )

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

                if atr is None or atr <= 0:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                support, resistance, tolerance = (
                    detect_zones(
                        candles,
                        atr
                    )
                )

                side, zone, reason = get_signal(
                    candles,
                    support,
                    resistance,
                    tolerance,
                    atr
                )

                state["last_candle"] = (
                    candle_time
                )

                save_state(state)

                trend, trend_reason = m5_trend(
                    candles
                )

                print(
                    "\nM1 CHECK:",
                    candle_time,
                    "\nCLOSE:",
                    candles[-1]["close"],
                    "\nATR:",
                    round(atr, 2),
                    "\nM5 TREND:",
                    trend,
                    "\nRESULT:",
                    side or reason,
                    flush=True
                )

                # =================================
                # 6. ENTRY
                # =================================

                if side is not None:

                    signal_key = (
                        f"{candle_time}:{side}"
                    )

                    if (
                        signal_key
                        != state["last_signal"]
                    ):

                        state["last_signal"] = (
                            signal_key
                        )

                        save_state(state)

                        notify(
                            "FAST SIGNAL V6\n"
                            f"SIDE: {side}\n"
                            f"REASON: {reason}\n"
                            f"ZONE: {zone:.2f}\n"
                            f"ATR14: {atr:.2f}\n"
                            f"M5: {trend}"
                        )

                        await open_trade(
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

            except MetaConnectionError:

                # Pri RPC chybe obnovime celu relaciu.
                # Presny nazov zlyhaneho prikazu
                # je uz zapisany v meta_call().
                raise

            except Exception as e:

                print(
                    "LOOP ERROR:",
                    type(e).__name__,
                    str(e),
                    flush=True
                )

                # Chyba historickych sviecok
                # nema automaticky rusit RPC relaciu.
                # Iba vynechame tento cyklus.
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
        "RIOBOT GOLD V6 START\n"
        "FAST M1 + M5 TREND\n"
        "ATR SL + EARLY BE\n"
        "METAAPI DIAGNOSTICS ACTIVE\n"
        f"LOT: {LOT_SIZE}\n"
        f"MAX TRADES: {MAX_TRADES}\n"
        f"COUNT: {state['trade_count']}\n"
        f"LIVE TRADING: {ENABLE_TRADING}\n"
        f"SAFETY HALT: {state['halted']}\n"
        "TP1 LOCK + TRAILING ACTIVE"
    )

    reconnect_attempt = 0

    while True:

        try:

            await bot_session(state)

            reconnect_attempt = 0

        except Exception as e:

            reconnect_attempt += 1

            delay = min(
                RECONNECT_SECONDS
                * reconnect_attempt,
                60
            )

            print(
                "SESSION ERROR:",
                traceback.format_exc(),
                flush=True
            )

            notify(
                "RIOBOT CONNECTION ERROR\n"
                f"{type(e).__name__}: {str(e)[:220]}\n"
                f"RECONNECT IN {delay} SECONDS"
            )

            await asyncio.sleep(delay)


# =====================================================
# START
# =====================================================

if __name__ == "__main__":

    asyncio.run(main())
