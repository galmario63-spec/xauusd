
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
# RIOBOT GOLD ZONES M1 - CONNECTION FIX
# =====================================================

SYMBOL = "XAUUSD"

LOT_SIZE = 0.01
MAX_TRADES = 4

ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "false")
    .strip().lower() == "true"
)

COMMENT = "RIO ZONES M1"

LOOP_SECONDS = 5
RECONNECT_SECONDS = 3

META_TIMEOUT = 18
SYNC_TIMEOUT = 90

COOLDOWN_SECONDS = 180

# No persistent disk required.
# Counter applies to the current runtime only.
STATE_FILE = "/tmp/rio_zones_state.json"


# =====================================================
# ZONES
# =====================================================

ZONE_LOOKBACK = 30
ZONE_TOLERANCE = 0.35
MIN_TOUCHES = 2

MIN_BODY_RATIO = 0.35
MIN_WICK_RATIO = 0.25


# =====================================================
# ATR / RISK
# =====================================================

ATR_PERIOD = 14

SL_ATR_BUFFER = 0.35

MIN_SL_DISTANCE = 0.40
MAX_SL_DISTANCE = 2.00

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
# ENVIRONMENT
# =====================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =====================================================
# RENDER SERVER
# =====================================================

app = Flask(__name__)


@app.route("/")
def home():

    return "RIOBOT GOLD ZONES ACTIVE", 200


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

        response = requests.post(
            f"https://api.telegram.org/bot"
            f"{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=5
        )

        if response.status_code != 200:

            print(
                "TELEGRAM HTTP ERROR:",
                response.status_code,
                flush=True
            )

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            type(e).__name__,
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
        "tp1_reached": False,
        "had_position": False,
        "halted": False,
        "order_uncertain": False
    }


def save_state(state):

    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w") as f:

        json.dump(
            state,
            f
        )

        f.flush()
        os.fsync(f.fileno())

    os.replace(
        temporary,
        STATE_FILE
    )


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

    return state


# =====================================================
# METAAPI WRAPPER
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

        response = requests.get(
            url,
            headers={
                "auth-token": M_TOKEN
            },
            timeout=12
        )

        response.raise_for_status()

        return response.json()

    raw = await asyncio.wait_for(
        asyncio.to_thread(fetch),
        timeout=15
    )

    if not isinstance(raw, list):

        raise RuntimeError(
            "INVALID CANDLE DATA"
        )

    now = datetime.now(timezone.utc)

    candles = []

    for candle in raw:

        try:

            dt = datetime.fromisoformat(
                str(candle["time"]).replace(
                    "Z",
                    "+00:00"
                )
            )

            if dt.tzinfo is None:

                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            age = (
                now - dt
            ).total_seconds()

            # Closed M1 candle only.
            if age < 65:
                continue

            candles.append({
                "time": dt.isoformat(),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"])
            })

        except (
            KeyError,
            ValueError,
            TypeError
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

    if candles:

        last = datetime.fromisoformat(
            candles[-1]["time"]
        )

        age = (
            now - last
        ).total_seconds()

        if age > 180:

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

    return (
        sum(values[-ATR_PERIOD:])
        / ATR_PERIOD
    )


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

    for i in range(
        2,
        len(history) - 2
    ):

        c = history[i]

        window = history[
            i - 2:i + 3
        ]

        if c["low"] <= min(
            x["low"] for x in window
        ):

            lows.append(c["low"])

        if c["high"] >= max(
            x["high"] for x in window
        ):

            highs.append(c["high"])

    def valid(values):

        result = []

        for value in values:

            touches = sum(
                abs(v - value)
                <= ZONE_TOLERANCE
                for v in values
            )

            if touches >= MIN_TOUCHES:

                result.append(value)

        return result

    current = candles[-1]

    supports = [
        x for x in valid(lows)
        if abs(current["low"] - x)
        <= ZONE_TOLERANCE
    ]

    resistances = [
        x for x in valid(highs)
        if abs(current["high"] - x)
        <= ZONE_TOLERANCE
    ]

    support = None
    resistance = None

    if supports:

        support = min(
            supports,
            key=lambda x: abs(
                current["low"] - x
            )
        )

    if resistances:

        resistance = min(
            resistances,
            key=lambda x: abs(
                current["high"] - x
            )
        )

    return support, resistance


# =====================================================
# CONFIRMED REJECTION
# =====================================================

def get_signal(
    candles,
    support,
    resistance
):

    c = candles[-1]
    p = candles[-2]

    candle_range = (
        c["high"] - c["low"]
    )

    if candle_range <= 0:

        return None

    body = abs(
        c["close"] - c["open"]
    )

    if (
        body / candle_range
        < MIN_BODY_RATIO
    ):

        return None

    lower_wick = (
        min(c["open"], c["close"])
        - c["low"]
    )

    upper_wick = (
        c["high"]
        - max(c["open"], c["close"])
    )

    # BUY

    if support is not None:

        bullish = (
            c["close"] > c["open"]
            and c["close"] > p["close"]
            and c["close"] > support
        )

        wick = (
            lower_wick / candle_range
            >= MIN_WICK_RATIO
        )

        if bullish and wick:

            return "BUY"

    # SELL

    if resistance is not None:

        bearish = (
            c["close"] < c["open"]
            and c["close"] < p["close"]
            and c["close"] < resistance
        )

        wick = (
            upper_wick / candle_range
            >= MIN_WICK_RATIO
        )

        if bearish and wick:

            return "SELL"

    return None


# =====================================================
# NEWS FILTER
# =====================================================

def fetch_news():

    response = requests.get(
        NEWS_URL,
        timeout=10
    )

    response.raise_for_status()

    raw = response.json()

    if not isinstance(raw, list):

        raise RuntimeError(
            "INVALID NEWS DATA"
        )

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
            ValueError,
            TypeError
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
                timeout=13
            )

            news_cache["events"] = events

            news_cache["updated"] = now

        except Exception as e:

            print(
                "NEWS ERROR:",
                type(e).__name__,
                flush=True
            )

    if (
        now - news_cache["updated"]
        > NEWS_MAX_AGE
    ):

        print(
            "NEWS DATA UNAVAILABLE - NO ENTRY",
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

        raise RuntimeError(
            "MARKET NOT READY"
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
        bid <= 0
        or ask <= bid
        or tick <= 0
    ):

        raise RuntimeError(
            "INVALID MARKET PRICE"
        )

    return {
        "bid": bid,
        "ask": ask,
        "tick": tick,
        "digits": digits,
        "spec": spec
    }


def normalize(price, market):

    tick = market["tick"]

    return round(
        round(price / tick) * tick,
        market["digits"]
    )


async def get_positions(connection):

    positions = await meta_call(
        connection.get_positions()
    )

    if not isinstance(positions, list):

        raise RuntimeError(
            "INVALID POSITIONS RESPONSE"
        )

    return [
        p for p in positions
        if str(
            p.get("symbol", "")
        ).upper() == SYMBOL
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
# INITIAL SL / TP1
# =====================================================

def get_levels(
    side,
    entry,
    support,
    resistance,
    atr,
    market
):

    buffer = max(
        atr * SL_ATR_BUFFER,
        market["tick"] * 5
    )

    if side == "BUY":

        if support is None:
            return None

        sl = support - buffer

    else:

        if resistance is None:
            return None

        sl = resistance + buffer

    sl = normalize(
        sl,
        market
    )

    risk = abs(
        entry - sl
    )

    if not (
        MIN_SL_DISTANCE
        <= risk
        <= MAX_SL_DISTANCE
    ):

        return None

    direction = (
        1 if side == "BUY" else -1
    )

    tp1 = normalize(
        entry + direction * risk * TP1_RR,
        market
    )

    return {
        "sl": sl,
        "tp1": tp1,
        "risk": risk
    }


# =====================================================
# OPEN TRADE
# =====================================================

async def open_trade(
    connection,
    side,
    support,
    resistance,
    atr,
    state
):

    if (
        state["trade_count"] >= MAX_TRADES
        or state["halted"]
        or state["order_uncertain"]
    ):

        return False

    existing = await get_positions(
        connection
    )

    if existing:

        return False

    market = await get_market(
        connection
    )

    spread = (
        market["ask"] - market["bid"]
    )

    if spread > MAX_SPREAD:

        print(
            "HIGH SPREAD - NO ENTRY",
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
            "LOT 0.01 NOT ALLOWED"
        )

        return False

    steps = LOT_SIZE / volume_step

    if abs(
        steps - round(steps)
    ) > 1e-7:

        notify(
            "INVALID VOLUME STEP"
        )

        return False

    entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )

    levels = get_levels(
        side,
        entry,
        support,
        resistance,
        atr,
        market
    )

    if levels is None:

        print(
            "SL DISTANCE FILTER",
            flush=True
        )

        return False

    sl = levels["sl"]

    point = float(
        spec.get("point")
        or 10 ** (-market["digits"])
    )

    stops_level = float(
        spec.get("stopsLevel") or 0
    )

    minimum = (
        stops_level * point
        + market["tick"]
    )

    if side == "BUY":

        if (
            market["bid"] - sl
            <= minimum
        ):

            return False

    else:

        if (
            sl - market["ask"]
            <= minimum
        ):

            return False

    number = (
        state["trade_count"] + 1
    )

    message = (
        "RIO GOLD ZONES M1\n"
        f"TRADE: {number}/{MAX_TRADES}\n"
        f"SIGNAL: {side}\n"
        f"LOT: {LOT_SIZE}\n"
        f"ENTRY: {entry}\n"
        f"SL: {sl}\n"
        f"TP1 TARGET: {levels['tp1']}\n"
        "TP OPEN + BE ACTIVE"
    )

    if not ENABLE_TRADING:

        notify(
            "TEST SIGNAL - NO ORDER\n"
            + message
        )

        return False

    # Prevent automatic duplicate submission.
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

        # Broker confirmation is followed
        # by an actual position check.
        await asyncio.sleep(2)

        positions = await get_positions(
            connection
        )

        matches = [
            p for p in positions
            if str(
                p.get("comment", "")
            ) == COMMENT
            and position_side(p) == side
        ]

        if len(matches) != 1:

            state["halted"] = True

            save_state(state)

            notify(
                "ORDER VERIFICATION FAILED\n"
                "CHECK MT5 HISTORY.\n"
                "NEW ENTRIES BLOCKED."
            )

            return False

        position = matches[0]

        state["trade_count"] += 1

        state["trade_risk"] = levels["risk"]

        state["position_id"] = str(
            position["id"]
        )

        state["tp1_reached"] = False
        state["had_position"] = True

        state["order_uncertain"] = False

        if state["trade_count"] >= MAX_TRADES:

            state["halted"] = True

        save_state(state)

        notify(
            "ORDER OK\n\n"
            + message
        )

        if state["trade_count"] >= MAX_TRADES:

            notify(
                "FOUR TRADES REACHED\n"
                "NEW ENTRIES STOPPED."
            )

        return True

    except Exception as e:

        state["halted"] = True

        save_state(state)

        notify(
            "ORDER RESULT UNCERTAIN\n"
            f"{type(e).__name__}: {e}\n"
            "NEW ENTRIES BLOCKED.\n"
            "CHECK MT5 HISTORY."
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

    side = position_side(
        position
    )

    if side is None:

        return

    if str(
        position["id"]
    ) != str(
        state["position_id"]
    ):

        return

    market = await get_market(
        connection
    )

    pid = position["id"]

    entry = float(
        position["openPrice"]
    )

    current_sl = float(
        position.get("stopLoss") or 0
    )

    if current_sl <= 0:

        notify(
            "WARNING: POSITION WITHOUT SL"
        )

        state["halted"] = True

        save_state(state)

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

    risk = state["trade_risk"]

    if not risk or risk <= 0:

        print(
            "ORIGINAL RISK UNKNOWN",
            flush=True
        )

        return

    wanted_sl = current_sl

    stage = None

    # BREAK EVEN

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

        stage = "BE"

    # TP1

    if (
        profit_distance
        >= risk * TP1_RR
    ):

        lock = (
            entry
            + direction * risk * 0.50
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

        stage = "TP1"

    # TRAILING

    if (
        profit_distance
        >= risk * TRAIL_TRIGGER_RR
        and atr is not None
        and atr > 0
    ):

        distance = (
            atr * TRAIL_ATR_MULTIPLIER
        )

        trail = (
            price - direction * distance
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

    minimum = (
        max(stops, freeze) * point
        + tick
    )

    if side == "BUY":

        if (
            wanted_sl
            >= market["bid"] - minimum
        ):

            return

    else:

        if (
            wanted_sl
            <= market["ask"] + minimum
        ):

            return

    try:

        await meta_call(
            connection.modify_position(
                pid,
                wanted_sl,
                position.get("takeProfit")
            )
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

        notify(
            "RIO GOLD SL UPDATED\n"
            f"SIDE: {side}\n"
            f"STAGE: {stage}\n"
            f"NEW SL: {wanted_sl}"
        )

    except Exception as e:

        print(
            "SL ERROR:",
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

        current = await get_positions(
            connection
        )

        notify(
            "RIO GOLD ZONES CONNECTED\n"
            f"LOT: {LOT_SIZE}\n"
            f"TRADES: {state['trade_count']}/{MAX_TRADES}\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        # After reconnect, check positions first.

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
                    "NEW ENTRIES BLOCKED.\n"
                    "CHECK MT5."
                )

        if state["order_uncertain"]:

            state["halted"] = True

            save_state(state)

            notify(
                "UNCERTAIN PREVIOUS ORDER\n"
                "CHECK MT5 HISTORY."
            )

        errors = 0

        last_atr = None
        last_atr_time = 0

        while True:

            try:

                # =================================================
                # CHECK POSITIONS FIRST
                # =================================================

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
                                "ATR FETCH ERROR:",
                                type(e).__name__,
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

                # =================================================
                # CLOSED POSITION
                # =================================================

                if state["had_position"]:

                    state["had_position"] = False

                    state["last_trade_time"] = time.time()

                    state["trade_risk"] = None

                    state["position_id"] = None

                    state["tp1_reached"] = False

                    save_state(state)

                    notify(
                        "POSITION CLOSED\n"
                        "COOLDOWN 180 SECONDS"
                    )

                # =================================================
                # ENTRY SAFETY
                # =================================================

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
                    time.time()
                    - state["last_trade_time"]
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

                # =================================================
                # M1 DATA
                # =================================================

                candles = await get_candles(
                    region
                )

                if (
                    len(candles)
                    < ZONE_LOOKBACK + 2
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                atr = calculate_atr(
                    candles
                )

                if atr is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                last_atr = atr

                last_atr_time = time.time()

                # =================================================
                # SIGNAL
                # =================================================

                support, resistance = detect_zones(
                    candles
                )

                side = get_signal(
                    candles,
                    support,
                    resistance
                )

                if side:

                    signal_key = [
                        candles[-1]["time"],
                        side
                    ]

                    if (
                        signal_key
                        != state["last_signal"]
                    ):

                        state["last_signal"] = signal_key

                        save_state(state)

                        notify(
                            f"ZONE SIGNAL: {side}\n"
                            f"SUPPORT: {support}\n"
                            f"RESISTANCE: {resistance}\n"
                            f"ATR14: {atr:.2f}"
                        )

                        await open_trade(
                            connection,
                            side,
                            support,
                            resistance,
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
                timeout=5
            )

        except Exception:

            pass


# =====================================================
# MAIN / RECONNECT
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
        "NO PSAR\n"
        f"LOT: {LOT_SIZE}\n"
        f"MAX TRADES: {MAX_TRADES}\n"
        f"COUNT: {state['trade_count']}\n"
        f"LIVE TRADING: {ENABLE_TRADING}\n"
        "TP1 + TP OPEN + BE ACTIVE"
    )

    while True:

        try:

            await bot_session(
                state
            )

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
