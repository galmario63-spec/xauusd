
import os
import json
import time
import asyncio
import requests

from datetime import datetime, timezone
from threading import Thread
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =====================================================
# RIOBOT GOLD ZONES M1 - LIVE
# =====================================================

SYMBOL = "XAUUSD"
COMMENT = "RIO ZONES M1"

LOT_SIZE = 0.01
MAX_TRADES = 4

# REAL TRADING ENABLED
ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "true")
    .strip().lower() == "true"
)

LOOP_SECONDS = 5
RECONNECT_SECONDS = 3
META_TIMEOUT = 30
SYNC_TIMEOUT = 120

COOLDOWN_SECONDS = 180

# Render temporary storage.
# Persistent disk can be configured with RIO_STATE_DIR.
STATE_DIR = os.getenv("RIO_STATE_DIR", "/tmp")
STATE_FILE = os.path.join(
    STATE_DIR,
    "rio_gold_zones_live.json"
)

# =====================================================
# ZONES
# =====================================================

ZONE_LOOKBACK = 30
ZONE_TOLERANCE = 0.35
MIN_TOUCHES = 2

MIN_BODY_RATIO = 0.30
MIN_WICK_RATIO = 0.20

CONFIRM_BARS = 3
MAX_ENTRY_ATR = 1.20

# =====================================================
# ATR / RISK
# =====================================================

ATR_PERIOD = 14

SL_ATR_BUFFER = 0.35

MIN_SL_DISTANCE = 0.40
MAX_SL_DISTANCE = 3.50

MAX_SPREAD = 0.40

TP1_RR = 1.00

BE_TRIGGER_RR = 0.70
BE_LOCK_DISTANCE = 0.10

TRAIL_TRIGGER_RR = 1.50
TRAIL_ATR_MULTIPLIER = 1.00

# =====================================================
# NEWS
# =====================================================

NEWS_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

NEWS_BEFORE = 15
NEWS_AFTER = 30

NEWS_REFRESH = 1800
NEWS_MAX_AGE = 10800

news_cache = {
    "events": [],
    "updated": 0
}

# =====================================================
# ENV
# =====================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

# =====================================================
# RENDER
# =====================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIOBOT GOLD ZONES LIVE", 200


def keep_alive():

    def server():
        app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", 10000)),
            use_reloader=False
        )

    Thread(
        target=server,
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
        r = requests.post(
            f"https://api.telegram.org/bot"
            f"{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=8
        )

        if r.status_code != 200:
            print(
                "TELEGRAM HTTP:",
                r.status_code,
                flush=True
            )

    except Exception as e:
        print(
            "TELEGRAM ERROR:",
            str(e),
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
        "last_signal": None,
        "last_trade_time": 0,
        "trade_risk": None,
        "position_id": None,
        "had_position": False,
        "tp1_reached": False,
        "halted": False,
        "order_uncertain": False,
        "armed_side": None,
        "armed_zone": None,
        "armed_time": None,
        "last_checked_candle": None
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

    if os.path.exists(STATE_FILE):

        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)

            for key in state:
                if key in saved:
                    state[key] = saved[key]

        except Exception as e:
            print("STATE ERROR:", e, flush=True)
            state["halted"] = True

    return state


# =====================================================
# METAAPI
# =====================================================

async def meta_call(coroutine):

    return await asyncio.wait_for(
        coroutine,
        timeout=META_TIMEOUT
    )


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
        "?limit=100"
    )

    def fetch():

        r = requests.get(
            url,
            headers={"auth-token": M_TOKEN},
            timeout=15
        )

        r.raise_for_status()
        return r.json()

    raw = await asyncio.wait_for(
        asyncio.to_thread(fetch),
        timeout=20
    )

    if not isinstance(raw, list):
        raise RuntimeError("INVALID CANDLE DATA")

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
                dt = dt.replace(tzinfo=timezone.utc)

            age = (now - dt).total_seconds()

            if age < 62:
                continue

            candles.append({
                "time": dt.isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

        except (KeyError, ValueError, TypeError):
            continue

    unique = {
        c["time"]: c
        for c in candles
    }

    candles = sorted(
        unique.values(),
        key=lambda c: c["time"]
    )

    if not candles:
        raise RuntimeError("NO M1 CANDLES")

    last = datetime.fromisoformat(
        candles[-1]["time"]
    )

    if (now - last).total_seconds() > 180:
        raise RuntimeError("STALE M1 DATA")

    return candles


# =====================================================
# ATR
# =====================================================

def calculate_atr(candles):

    if len(candles) < ATR_PERIOD + 2:
        return None

    values = []

    for i in range(1, len(candles)):

        c = candles[i]
        p = candles[i - 1]

        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"])
        )

        values.append(tr)

    return sum(
        values[-ATR_PERIOD:]
    ) / ATR_PERIOD


# =====================================================
# BUY / SELL ZONES
# =====================================================

def detect_zones(candles):

    history = candles[
        -(ZONE_LOOKBACK + 1):-1
    ]

    if len(history) < ZONE_LOOKBACK:
        return None, None

    lows = []
    highs = []

    for i in range(2, len(history) - 2):

        c = history[i]
        window = history[i - 2:i + 3]

        if c["low"] <= min(
            x["low"] for x in window
        ):
            lows.append(c["low"])

        if c["high"] >= max(
            x["high"] for x in window
        ):
            highs.append(c["high"])

    def valid(values):

        return [
            value
            for value in values
            if sum(
                abs(x - value) <= ZONE_TOLERANCE
                for x in values
            ) >= MIN_TOUCHES
        ]

    current = candles[-1]

    supports = valid(lows)
    resistances = valid(highs)

    support = (
        min(
            supports,
            key=lambda x: abs(
                current["low"] - x
            )
        )
        if supports else None
    )

    resistance = (
        min(
            resistances,
            key=lambda x: abs(
                current["high"] - x
            )
        )
        if resistances else None
    )

    return support, resistance


# =====================================================
# SIGNAL
# =====================================================

def clear_arm(state):

    state["armed_side"] = None
    state["armed_zone"] = None
    state["armed_time"] = None


def candle_index(candles, timestamp):

    for i, c in enumerate(candles):
        if c["time"] == timestamp:
            return i

    return -1


def detect_signal(
    candles,
    support,
    resistance,
    atr,
    state
):

    c = candles[-1]
    p = candles[-2]

    candle_range = c["high"] - c["low"]

    if candle_range <= 0:
        return None, None, "ZERO RANGE"

    body_ratio = (
        abs(c["close"] - c["open"])
        / candle_range
    )

    lower_wick = (
        min(c["open"], c["close"])
        - c["low"]
    ) / candle_range

    upper_wick = (
        c["high"]
        - max(c["open"], c["close"])
    ) / candle_range

    buy_touch = (
        support is not None
        and abs(c["low"] - support)
        <= ZONE_TOLERANCE
    )

    sell_touch = (
        resistance is not None
        and abs(c["high"] - resistance)
        <= ZONE_TOLERANCE
    )

    armed_side = state["armed_side"]
    armed_zone = state["armed_zone"]
    armed_time = state["armed_time"]

    # Previously touched zone.

    if armed_side and armed_time:

        index = candle_index(
            candles,
            armed_time
        )

        bars = (
            len(candles) - 1 - index
            if index >= 0
            else CONFIRM_BARS + 1
        )

        if bars > CONFIRM_BARS:
            clear_arm(state)

        elif armed_zone is not None:

            if armed_side == "BUY":

                if c["close"] < armed_zone - atr:
                    clear_arm(state)

                elif (
                    c["close"] > c["open"]
                    and c["close"] > p["close"]
                    and c["close"] > armed_zone
                    and body_ratio >= MIN_BODY_RATIO
                    and c["close"] - armed_zone
                    <= atr * MAX_ENTRY_ATR
                ):

                    zone = armed_zone
                    clear_arm(state)

                    return (
                        "BUY",
                        zone,
                        "SUPPORT CONFIRMED"
                    )

            elif armed_side == "SELL":

                if c["close"] > armed_zone + atr:
                    clear_arm(state)

                elif (
                    c["close"] < c["open"]
                    and c["close"] < p["close"]
                    and c["close"] < armed_zone
                    and body_ratio >= MIN_BODY_RATIO
                    and armed_zone - c["close"]
                    <= atr * MAX_ENTRY_ATR
                ):

                    zone = armed_zone
                    clear_arm(state)

                    return (
                        "SELL",
                        zone,
                        "RESISTANCE CONFIRMED"
                    )

    # Immediate BUY rejection.

    if buy_touch and (
        c["close"] > c["open"]
        and c["close"] > support
        and body_ratio >= MIN_BODY_RATIO
        and lower_wick >= MIN_WICK_RATIO
    ):

        clear_arm(state)

        return (
            "BUY",
            support,
            "BUY REJECTION"
        )

    # Immediate SELL rejection.

    if sell_touch and (
        c["close"] < c["open"]
        and c["close"] < resistance
        and body_ratio >= MIN_BODY_RATIO
        and upper_wick >= MIN_WICK_RATIO
    ):

        clear_arm(state)

        return (
            "SELL",
            resistance,
            "SELL REJECTION"
        )

    if buy_touch and not sell_touch:

        state["armed_side"] = "BUY"
        state["armed_zone"] = support
        state["armed_time"] = c["time"]

        return None, None, "BUY ZONE ARMED"

    if sell_touch and not buy_touch:

        state["armed_side"] = "SELL"
        state["armed_zone"] = resistance
        state["armed_time"] = c["time"]

        return None, None, "SELL ZONE ARMED"

    return None, None, "WAITING"


# =====================================================
# NEWS
# =====================================================

def fetch_news():

    r = requests.get(
        NEWS_URL,
        timeout=12
    )

    r.raise_for_status()
    raw = r.json()

    if not isinstance(raw, list):
        raise RuntimeError("INVALID NEWS DATA")

    events = []

    for event in raw:

        country = str(
            event.get("country")
            or event.get("currency")
            or ""
        ).upper()

        impact = str(
            event.get("impact", "")
        ).lower()

        if country != "USD":
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
                dt = dt.replace(tzinfo=timezone.utc)

            events.append(dt.timestamp())

        except (KeyError, ValueError, TypeError):
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
            news_cache["updated"] = now

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

        print(
            "NEWS UNAVAILABLE - NO ENTRY",
            flush=True
        )

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
# MARKET / POSITIONS
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
        raise RuntimeError("MARKET NOT READY")

    digits = int(spec.get("digits", 2))

    tick = float(
        spec.get("tickSize")
        or 10 ** (-digits)
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    if bid <= 0 or ask <= bid or tick <= 0:
        raise RuntimeError("INVALID MARKET PRICE")

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
        connection.get_positions()
    )

    if not isinstance(positions, list):
        raise RuntimeError("INVALID POSITIONS")

    return [
        p for p in positions
        if str(p.get("symbol", "")).upper()
        == SYMBOL
    ]


def position_side(position):

    ptype = str(
        position.get("type", "")
    ).upper()

    if ptype in (
        "POSITION_TYPE_BUY",
        "ORDER_TYPE_BUY",
        "BUY",
        "0"
    ):
        return "BUY"

    if ptype in (
        "POSITION_TYPE_SELL",
        "ORDER_TYPE_SELL",
        "SELL",
        "1"
    ):
        return "SELL"

    return None


# =====================================================
# SL / TP1
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
        sl = zone - buffer
    else:
        sl = zone + buffer

    sl = normalize(sl, market)

    risk = abs(entry - sl)

    if risk < MIN_SL_DISTANCE:
        return None, (
            f"SL TOO SMALL: {risk:.2f}"
        )

    if risk > MAX_SL_DISTANCE:
        return None, (
            f"SL TOO LARGE: {risk:.2f}"
        )

    if side == "BUY" and sl >= entry:
        return None, "INVALID BUY SL"

    if side == "SELL" and sl <= entry:
        return None, "INVALID SELL SL"

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
# OPEN REAL TRADE
# =====================================================

async def open_trade(
    connection,
    side,
    zone,
    atr,
    state
):

    if (
        state["halted"]
        or state["order_uncertain"]
        or state["trade_count"] >= MAX_TRADES
    ):
        return False

    existing = await get_positions(connection)

    if existing:
        print(
            "ENTRY BLOCKED: EXISTING POSITION",
            flush=True
        )
        return False

    market = await get_market(connection)

    spread = market["ask"] - market["bid"]

    if spread > MAX_SPREAD:
        print(
            f"ENTRY BLOCKED: SPREAD {spread:.2f}",
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

    if LOT_SIZE < min_volume:
        notify(
            f"LOT BLOCKED: MIN {min_volume}"
        )
        return False

    steps = LOT_SIZE / volume_step

    if abs(steps - round(steps)) > 1e-7:
        notify("INVALID VOLUME STEP")
        return False

    entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )

    if abs(entry - zone) > atr * MAX_ENTRY_ATR:
        print(
            "ENTRY BLOCKED: TOO FAR FROM ZONE",
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
            f"ZONE SIGNAL {side}\n"
            f"ORDER BLOCKED\n{reason}"
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

    minimum = (
        stops * point
        + market["tick"]
    )

    if side == "BUY":

        if market["bid"] - sl <= minimum:
            print(
                "BROKER BUY STOPS FILTER",
                flush=True
            )
            return False

    else:

        if sl - market["ask"] <= minimum:
            print(
                "BROKER SELL STOPS FILTER",
                flush=True
            )
            return False

    number = state["trade_count"] + 1

    message = (
        "RIO GOLD ZONES M1\n"
        f"TRADE: {number}/{MAX_TRADES}\n"
        f"SIGNAL: {side}\n"
        f"LOT: {LOT_SIZE}\n"
        f"ENTRY: {entry}\n"
        f"ZONE: {zone}\n"
        f"SL: {sl}\n"
        f"TP1 LEVEL: {levels['tp1']}\n"
        "TP OPEN + BE ACTIVE"
    )

    if not ENABLE_TRADING:

        notify(
            "TEST SIGNAL - NO ORDER\n\n"
            + message
        )

        return False

    state["order_uncertain"] = True
    save_state(state)

    try:

        options = {
            "comment": COMMENT
        }

        if side == "BUY":

            result = await meta_call(
                connection.create_market_buy_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    None,
                    options
                )
            )

        else:

            result = await meta_call(
                connection.create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    None,
                    options
                )
            )

        print(
            "ORDER RESPONSE:",
            result,
            flush=True
        )

        await asyncio.sleep(2)

        positions = await get_positions(connection)

        matches = [
            p for p in positions
            if (
                str(p.get("comment", "")) == COMMENT
                and position_side(p) == side
            )
        ]

        if len(matches) != 1:

            state["halted"] = True
            save_state(state)

            notify(
                "ORDER VERIFICATION FAILED\n"
                "CHECK MT5 HISTORY\n"
                "NEW ENTRIES BLOCKED"
            )

            return False

        position = matches[0]

        state["trade_count"] += 1

        state["position_id"] = str(
            position["id"]
        )

        state["trade_risk"] = levels["risk"]

        state["had_position"] = True
        state["tp1_reached"] = False

        state["order_uncertain"] = False

        if state["trade_count"] >= MAX_TRADES:
            state["halted"] = True

        save_state(state)

        notify(
            "ORDER OK\n\n" + message
        )

        if state["trade_count"] >= MAX_TRADES:

            notify(
                "FOUR TRADES REACHED\n"
                "NEW ENTRIES STOPPED"
            )

        return True

    except Exception as e:

        state["halted"] = True
        save_state(state)

        notify(
            "ORDER RESULT UNCERTAIN\n"
            f"{type(e).__name__}: {e}\n"
            "CHECK MT5\n"
            "NEW ENTRIES BLOCKED"
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
        print(
            "ORIGINAL RISK UNKNOWN",
            flush=True
        )
        return

    market = await get_market(connection)

    pid = position["id"]

    entry = float(position["openPrice"])

    current_sl = float(
        position.get("stopLoss") or 0
    )

    if current_sl <= 0:

        state["halted"] = True
        save_state(state)

        notify(
            "WARNING: POSITION WITHOUT SL\n"
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

    # BREAK EVEN

    if profit_distance >= risk * BE_TRIGGER_RR:

        be = (
            entry
            + direction * BE_LOCK_DISTANCE
        )

        if side == "BUY":
            wanted_sl = max(wanted_sl, be)
        else:
            wanted_sl = min(wanted_sl, be)

        stage = "BE"

    # TP1: lock half the original risk.
    # Position remains open.

    if profit_distance >= risk * TP1_RR:

        lock = (
            entry
            + direction * risk * 0.50
        )

        if side == "BUY":
            wanted_sl = max(wanted_sl, lock)
        else:
            wanted_sl = min(wanted_sl, lock)

        stage = "TP1"

    # TRAILING

    if (
        profit_distance >= risk * TRAIL_TRIGGER_RR
        and atr is not None
        and atr > 0
    ):

        trail = (
            price
            - direction * atr * TRAIL_ATR_MULTIPLIER
        )

        if side == "BUY":
            wanted_sl = max(wanted_sl, trail)
        else:
            wanted_sl = min(wanted_sl, trail)

        stage = "TRAILING"

    wanted_sl = normalize(
        wanted_sl,
        market
    )

    tick = market["tick"]

    if side == "BUY":

        if wanted_sl <= current_sl + tick / 2:
            return

    else:

        if wanted_sl >= current_sl - tick / 2:
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

    minimum = (
        max(stops, freeze) * point
        + tick
    )

    if side == "BUY":

        if wanted_sl >= market["bid"] - minimum:
            return

    else:

        if wanted_sl <= market["ask"] + minimum:
            return

    try:

        await meta_call(
            connection.modify_position(
                pid,
                wanted_sl,
                position.get("takeProfit")
            )
        )

        notify(
            "RIO GOLD SL UPDATED\n"
            f"SIDE: {side}\n"
            f"STAGE: {stage}\n"
            f"NEW SL: {wanted_sl}"
        )

        if (
            stage in ("TP1", "TRAILING")
            and not state["tp1_reached"]
        ):

            state["tp1_reached"] = True
            save_state(state)

            notify(
                "RIO GOLD TP1 REACHED\n"
                "TP OPEN CONTINUES"
            )

    except Exception as e:

        print(
            "SL MODIFY ERROR:",
            type(e).__name__,
            str(e),
            flush=True
        )


# =====================================================
# METAAPI SESSION
# =====================================================

async def bot_session(state):

    api = MetaApi(M_TOKEN)

    account = await meta_call(
        api.metatrader_account_api.get_account(
            M_ACC
        )
    )

    region = (
        getattr(account, "region", None)
        or "london"
    )

    if str(account.state).upper() != "DEPLOYED":

        await meta_call(
            account.deploy()
        )

    await asyncio.wait_for(
        account.wait_connected(),
        timeout=SYNC_TIMEOUT
    )

    connection = account.get_rpc_connection()

    try:

        await meta_call(
            connection.connect()
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=SYNC_TIMEOUT
        )

        current = await get_positions(connection)

        notify(
            "RIO GOLD ZONES CONNECTED\n"
            f"LOT: {LOT_SIZE}\n"
            f"TRADES: {state['trade_count']}/{MAX_TRADES}\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        if current:

            state["had_position"] = True

            if (
                len(current) != 1
                or state["position_id"] is None
                or str(current[0]["id"])
                != str(state["position_id"])
            ):

                state["halted"] = True
                save_state(state)

                notify(
                    "UNKNOWN OPEN POSITION\n"
                    "CHECK MT5\n"
                    "NEW ENTRIES BLOCKED"
                )

        if state["order_uncertain"]:

            state["halted"] = True
            save_state(state)

            notify(
                "PREVIOUS ORDER UNCERTAIN\n"
                "CHECK MT5 HISTORY"
            )

        errors = 0

        last_atr = None
        last_atr_time = 0

        while True:

            try:

                # POSITION MANAGEMENT FIRST

                current = await get_positions(
                    connection
                )

                if current:

                    state["had_position"] = True

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
                                "ATR ERROR:",
                                str(e),
                                flush=True
                            )

                    for position in current:

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

                    errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # POSITION CLOSED

                if state["had_position"]:

                    state["had_position"] = False

                    state["last_trade_time"] = time.time()

                    state["trade_risk"] = None
                    state["position_id"] = None
                    state["tp1_reached"] = False

                    clear_arm(state)

                    save_state(state)

                    notify(
                        "POSITION CLOSED\n"
                        "COOLDOWN 180 SECONDS"
                    )

                # ENTRY SAFETY

                if (
                    state["trade_count"] >= MAX_TRADES
                    or state["halted"]
                    or state["order_uncertain"]
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                if (
                    time.time() - state["last_trade_time"]
                    < COOLDOWN_SECONDS
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                if await news_blocked():

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # M1 DATA

                candles = await get_candles(region)

                if len(candles) < ZONE_LOOKBACK + 2:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                atr = calculate_atr(candles)

                if atr is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                last_atr = atr
                last_atr_time = time.time()

                candle_time = candles[-1]["time"]

                if (
                    candle_time
                    == state["last_checked_candle"]
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                state["last_checked_candle"] = candle_time

                # SIGNAL

                support, resistance = detect_zones(
                    candles
                )

                side, zone, reason = detect_signal(
                    candles,
                    support,
                    resistance,
                    atr,
                    state
                )

                print(
                    "M1 CHECK:",
                    candle_time,
                    "CLOSE:",
                    candles[-1]["close"],
                    "SUPPORT:",
                    support,
                    "RESISTANCE:",
                    resistance,
                    "ATR:",
                    round(atr, 2),
                    "RESULT:",
                    reason,
                    flush=True
                )

                save_state(state)

                if side is not None:

                    signal_key = [
                        candle_time,
                        side
                    ]

                    if signal_key != state["last_signal"]:

                        state["last_signal"] = signal_key
                        save_state(state)

                        notify(
                            f"ZONE SIGNAL: {side}\n"
                            f"ZONE: {zone}\n"
                            f"REASON: {reason}\n"
                            f"ATR14: {atr:.2f}"
                        )

                        await open_trade(
                            connection,
                            side,
                            zone,
                            atr,
                            state
                        )

                errors = 0

                await asyncio.sleep(
                    LOOP_SECONDS
                )

            except (
                asyncio.TimeoutError,
                TimeoutError,
                ConnectionError
            ) as e:

                print(
                    "METAAPI TIMEOUT:",
                    type(e).__name__,
                    flush=True
                )

                raise RuntimeError(
                    "METAAPI RECONNECT REQUIRED"
                ) from e

            except Exception as e:

                errors += 1

                print(
                    "LOOP ERROR:",
                    type(e).__name__,
                    str(e),
                    flush=True
                )

                if errors >= 3:
                    raise

                await asyncio.sleep(
                    LOOP_SECONDS
                )

    finally:

        try:

            await asyncio.wait_for(
                connection.close(),
                timeout=8
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
            "M_TOKEN OR M_ACC MISSING"
        )

        return

    state = load_state()

    telegram(
        "RIOBOT GOLD ZONES M1 START\n"
        "LIVE ZONES / NO PSAR\n"
        f"LOT: {LOT_SIZE}\n"
        f"MAX TRADES: {MAX_TRADES}\n"
        f"COUNT: {state['trade_count']}\n"
        f"LIVE TRADING: {ENABLE_TRADING}\n"
        "TP1 + TP OPEN + BE ACTIVE"
    )

    while True:

        try:

            await bot_session(state)

        except Exception as e:

            telegram(
                "RIOBOT CONNECTION ERROR\n"
                f"{type(e).__name__}: {e}\n"
                "RECONNECT IN 3 SECONDS"
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


# =====================================================
# START
# =====================================================

if __name__ == "__main__":
    asyncio.run(main())
