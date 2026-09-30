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
# RIOBOT GOLD ZONES M1 - V3 COMPLETE
# =====================================================

SYMBOL = "XAUUSD"
COMMENT = "RIO GOLD ZONES V3"

LOT_SIZE = 0.01
MAX_TRADES = 4

ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "false").lower() == "true"
)

LOOP_SECONDS = 5
RECONNECT_SECONDS = 5
META_TIMEOUT = 35
SYNC_TIMEOUT = 120
COOLDOWN_SECONDS = 180

ZONE_LOOKBACK = 35
ATR_PERIOD = 14

ZONE_ATR_TOLERANCE = 0.35
MIN_ZONE_TOLERANCE = 0.35

MIN_BODY_RATIO = 0.28
MIN_WICK_RATIO = 0.15
MAX_ENTRY_ATR = 1.20

SL_ATR_BUFFER = 0.30

MIN_SL_DISTANCE = 0.40
MAX_SL_DISTANCE = 3.50
MAX_SPREAD = 0.40

BE_TRIGGER_RR = 0.70
BE_LOCK_DISTANCE = 0.10

TP1_RR = 1.00
TP1_LOCK_RR = 0.50

TRAIL_TRIGGER_RR = 1.50
TRAIL_ATR_MULTIPLIER = 1.00

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
STATE_FILE = os.path.join(
    STATE_DIR,
    "rio_gold_zones_v3.json"
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
    return "RIOBOT GOLD V3 ACTIVE", 200


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
            f"https://api.telegram.org/bot"
            f"{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=8
        )

    except Exception as e:
        print("TELEGRAM ERROR:", e, flush=True)


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
        return state

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
# CANDLES
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
            headers={"auth-token": M_TOKEN},
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    raw = await asyncio.wait_for(
        asyncio.to_thread(fetch),
        timeout=20
    )

    if not isinstance(raw, list):
        raise RuntimeError("INVALID CANDLES RESPONSE")

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

            age = (now - dt).total_seconds()

            # Only completed M1 candles.
            if age < 60:
                continue

            candles.append({
                "time": dt.isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"])
            })

        except (KeyError, TypeError, ValueError):
            continue

    unique = {
        candle["time"]: candle
        for candle in candles
    }

    candles = sorted(
        unique.values(),
        key=lambda c: c["time"]
    )

    if len(candles) < ZONE_LOOKBACK + 3:
        raise RuntimeError("NOT ENOUGH M1 HISTORY")

    last_time = datetime.fromisoformat(
        candles[-1]["time"]
    )

    if (now - last_time).total_seconds() > 180:
        raise RuntimeError("STALE M1 DATA")

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
# SUPPORT / RESISTANCE
# =====================================================

def detect_zones(candles, atr):

    history = candles[
        -(ZONE_LOOKBACK + 2):-2
    ]

    if len(history) < ZONE_LOOKBACK:
        return None, None, None

    support = min(
        candle["low"]
        for candle in history
    )

    resistance = max(
        candle["high"]
        for candle in history
    )

    tolerance = max(
        MIN_ZONE_TOLERANCE,
        atr * ZONE_ATR_TOLERANCE
    )

    return support, resistance, tolerance


# =====================================================
# SIGNALS
# =====================================================

def get_signal(
    candles,
    support,
    resistance,
    tolerance,
    atr
):

    candle = candles[-1]
    previous = candles[-2]

    candle_range = (
        candle["high"] - candle["low"]
    )

    if candle_range <= 0:
        return None, None, "ZERO RANGE"

    body = abs(
        candle["close"] - candle["open"]
    )

    body_ratio = body / candle_range

    lower_wick = (
        min(candle["open"], candle["close"])
        - candle["low"]
    ) / candle_range

    upper_wick = (
        candle["high"]
        - max(candle["open"], candle["close"])
    ) / candle_range

    bullish = (
        candle["close"] > candle["open"]
        and body_ratio >= MIN_BODY_RATIO
    )

    bearish = (
        candle["close"] < candle["open"]
        and body_ratio >= MIN_BODY_RATIO
    )

    buy_zone = (
        support is not None
        and candle["low"] <= support + tolerance
        and candle["high"] >= support - tolerance
    )

    sell_zone = (
        resistance is not None
        and candle["high"] >= resistance - tolerance
        and candle["low"] <= resistance + tolerance
    )

    if (
        buy_zone
        and bullish
        and lower_wick >= MIN_WICK_RATIO
        and candle["close"] > support
        and candle["close"] > previous["close"]
        and candle["close"] - support
        <= atr * MAX_ENTRY_ATR
    ):

        return (
            "BUY",
            support,
            "SUPPORT REJECTION"
        )

    if (
        sell_zone
        and bearish
        and upper_wick >= MIN_WICK_RATIO
        and candle["close"] < resistance
        and candle["close"] < previous["close"]
        and resistance - candle["close"]
        <= atr * MAX_ENTRY_ATR
    ):

        return (
            "SELL",
            resistance,
            "RESISTANCE REJECTION"
        )

    return None, None, "WAITING"


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
        raise RuntimeError("INVALID NEWS DATA")

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

        if "high" not in impact and "red" not in impact:
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

            events.append(dt.timestamp())

        except (KeyError, TypeError, ValueError):
            continue

    return events


async def news_blocked():

    now = time.time()

    if now - news_cache["updated"] >= NEWS_REFRESH:

        try:
            events = await asyncio.wait_for(
                asyncio.to_thread(fetch_news),
                timeout=16
            )

            news_cache["events"] = events
            news_cache["updated"] = now

        except Exception as e:
            print("NEWS ERROR:", e, flush=True)

    if (
        now - news_cache["updated"]
        > NEWS_MAX_AGE
    ):

        print(
            "ENTRY BLOCKED: NEWS UNAVAILABLE",
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
        raise RuntimeError("MARKET DATA NOT READY")

    digits = int(spec.get("digits", 2))

    tick = float(
        spec.get("tickSize")
        or 10 ** (-digits)
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    if tick <= 0 or bid <= 0 or ask <= bid:
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
        raise RuntimeError("INVALID POSITION DATA")

    return [
        position
        for position in positions
        if str(
            position.get("symbol", "")
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
# SL / TP1 LEVELS
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
        return None, f"SL TOO SMALL: {risk:.2f}"

    if risk > MAX_SL_DISTANCE:
        return None, f"SL TOO LARGE: {risk:.2f}"

    if side == "BUY" and sl >= entry:
        return None, "INVALID BUY SL"

    if side == "SELL" and sl <= entry:
        return None, "INVALID SELL SL"

    direction = 1 if side == "BUY" else -1

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
# OPEN TRADE
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

    positions = await get_positions(connection)

    if positions:
        print(
            "ENTRY BLOCKED: POSITION EXISTS",
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

    minimum_volume = float(
        spec.get("minVolume") or 0.01
    )

    volume_step = float(
        spec.get("volumeStep") or 0.01
    )

    if LOT_SIZE < minimum_volume:

        notify(
            f"LOT BLOCKED: MIN {minimum_volume}"
        )

        return False

    if abs(
        LOT_SIZE / volume_step
        - round(LOT_SIZE / volume_step)
    ) > 1e-7:

        notify("INVALID LOT STEP")
        return False

    entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )

    if abs(entry - zone) > atr * MAX_ENTRY_ATR:

        print(
            "ENTRY BLOCKED: PRICE TOO FAR",
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

    stop_level = float(
        spec.get("stopsLevel") or 0
    )

    minimum_distance = (
        stop_level * point
        + market["tick"]
    )

    if side == "BUY":

        if market["bid"] - sl <= minimum_distance:

            print(
                "ENTRY BLOCKED: BUY STOP DISTANCE",
                flush=True
            )

            return False

    else:

        if sl - market["ask"] <= minimum_distance:

            print(
                "ENTRY BLOCKED: SELL STOP DISTANCE",
                flush=True
            )

            return False

    message = (
        "RIO GOLD V3\n"
        f"SIDE: {side}\n"
        f"LOT: {LOT_SIZE}\n"
        f"ENTRY: {entry}\n"
        f"ZONE: {zone:.2f}\n"
        f"SL: {sl}\n"
        f"TP1 LEVEL: {levels['tp1']}\n"
        f"TRADE: {state['trade_count'] + 1}/{MAX_TRADES}"
    )

    if not ENABLE_TRADING:

        notify(
            "TEST SIGNAL - NO REAL ORDER\n"
            + message
        )

        return False

    # Save uncertainty BEFORE submitting the order.
    # This prevents accidental duplicate orders after timeout.

    state["order_uncertain"] = True
    save_state(state)

    try:

        options = {"comment": COMMENT}

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
                "CHECK MT5\n"
                "NEW ENTRIES STOPPED"
            )

            return False

        position = matches[0]

        state["trade_count"] += 1
        state["position_id"] = str(position["id"])
        state["trade_risk"] = levels["risk"]
        state["had_position"] = True
        state["tp1_reached"] = False
        state["order_uncertain"] = False
        state["last_trade_time"] = time.time()

        if state["trade_count"] >= MAX_TRADES:
            state["halted"] = True

        save_state(state)

        notify(
            "ORDER OK\n" + message
        )

        return True

    except Exception as e:

        state["halted"] = True
        save_state(state)

        notify(
            "ORDER UNCERTAIN\n"
            f"{type(e).__name__}: {e}\n"
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

    if str(position["id"]) != str(state["position_id"]):
        return

    risk = state["trade_risk"]

    if not risk or risk <= 0:
        return

    market = await get_market(connection)

    position_id = position["id"]

    entry = float(position["openPrice"])

    current_sl = float(
        position.get("stopLoss") or 0
    )

    if current_sl <= 0:

        state["halted"] = True
        save_state(state)

        notify(
            "WARNING: POSITION WITHOUT SL\n"
            "CHECK MT5"
        )

        return

    direction = 1 if side == "BUY" else -1

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

    # BE

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

    # TP1 is a milestone.
    # The full position stays open and SL locks 0.5R.

    if profit_distance >= risk * TP1_RR:

        lock = (
            entry
            + direction * risk * TP1_LOCK_RR
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

    wanted_sl = normalize(wanted_sl, market)

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

    minimum_distance = (
        max(stops, freeze) * point
        + tick
    )

    if side == "BUY":

        if wanted_sl >= market["bid"] - minimum_distance:
            return

    else:

        if wanted_sl <= market["ask"] + minimum_distance:
            return

    try:

        await meta_call(
            connection.modify_position(
                position_id,
                wanted_sl,
                position.get("takeProfit")
            )
        )

        notify(
            "RIO SL UPDATED\n"
            f"SIDE: {side}\n"
            f"STAGE: {stage}\n"
            f"SL: {wanted_sl}"
        )

        if (
            stage in ("TP1", "TRAILING")
            and not state["tp1_reached"]
        ):

            state["tp1_reached"] = True
            save_state(state)

            notify(
                "TP1 REACHED\n"
                "POSITION REMAINS OPEN"
            )

    except Exception as e:

        print(
            "SL MODIFY ERROR:",
            e,
            flush=True
        )


# =====================================================
# METAAPI SESSION
# =====================================================

async def bot_session(state):

    # IMPORTANT:
    # Python MetaApi SDK must not receive a manual region.

    api = MetaApi(M_TOKEN)

    account = await meta_call(
        api.metatrader_account_api.get_account(M_ACC)
    )

    region = getattr(account, "region", None) or "london"

    print(
        "METAAPI REGION:",
        region,
        flush=True
    )

    if str(account.state).upper() != "DEPLOYED":

        await meta_call(account.deploy())

    await asyncio.wait_for(
        account.wait_connected(),
        timeout=SYNC_TIMEOUT
    )

    connection = account.get_rpc_connection()

    try:

        await meta_call(connection.connect())

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=SYNC_TIMEOUT
        )

        positions = await get_positions(connection)

        notify(
            "RIO GOLD V3 CONNECTED\n"
            f"LOT: {LOT_SIZE}\n"
            f"TRADES: {state['trade_count']}/{MAX_TRADES}\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        # Reconcile open positions after reconnect.

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
                "CHECK MT5"
            )

        last_atr = None
        last_atr_time = 0

        while True:

            try:

                # =====================================
                # CHECK OPEN POSITIONS FIRST
                # =====================================

                positions = await get_positions(connection)

                if positions:

                    state["had_position"] = True

                    if time.time() - last_atr_time >= 60:

                        try:

                            candles = await get_candles(region)

                            last_atr = calculate_atr(candles)
                            last_atr_time = time.time()

                        except Exception as e:

                            print(
                                "ATR REFRESH ERROR:",
                                e,
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

                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                # =====================================
                # POSITION CLOSED
                # =====================================

                if state["had_position"]:

                    state["had_position"] = False
                    state["position_id"] = None
                    state["trade_risk"] = None
                    state["tp1_reached"] = False
                    state["last_trade_time"] = time.time()

                    save_state(state)

                    notify(
                        "POSITION CLOSED\n"
                        "COOLDOWN 180 SECONDS"
                    )

                # =====================================
                # ENTRY SAFETY
                # =====================================

                if state["halted"]:

                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                if state["order_uncertain"]:

                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                if state["trade_count"] >= MAX_TRADES:

                    state["halted"] = True
                    save_state(state)

                    notify(
                        "MAX 4 TRADES REACHED\n"
                        "NEW ENTRIES STOPPED"
                    )

                    continue

                if (
                    time.time() - state["last_trade_time"]
                    < COOLDOWN_SECONDS
                ):

                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                # =====================================
                # NEWS FILTER
                # =====================================

                if await news_blocked():

                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                # =====================================
                # M1 DATA
                # =====================================

                candles = await get_candles(region)

                candle_time = candles[-1]["time"]

                # Evaluate each completed candle once.

                if candle_time == state["last_candle"]:

                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                atr = calculate_atr(candles)

                if atr is None or atr <= 0:

                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                support, resistance, tolerance = detect_zones(
                    candles,
                    atr
                )

                side, zone, reason = get_signal(
                    candles,
                    support,
                    resistance,
                    tolerance,
                    atr
                )

                state["last_candle"] = candle_time
                save_state(state)

                print(
                    "\nM1 CHECK:",
                    candle_time,
                    "\nCLOSE:",
                    candles[-1]["close"],
                    "\nSUPPORT:",
                    round(support, 2)
                    if support is not None else None,
                    "\nRESISTANCE:",
                    round(resistance, 2)
                    if resistance is not None else None,
                    "\nATR:",
                    round(atr, 2),
                    "\nRESULT:",
                    side or reason,
                    flush=True
                )

                # =====================================
                # REAL SIGNAL
                # =====================================

                if side is not None:

                    signal_key = (
                        f"{candle_time}:{side}"
                    )

                    if signal_key != state["last_signal"]:

                        state["last_signal"] = signal_key
                        save_state(state)

                        notify(
                            "ZONE SIGNAL\n"
                            f"SIDE: {side}\n"
                            f"REASON: {reason}\n"
                            f"ZONE: {zone:.2f}\n"
                            f"ATR14: {atr:.2f}"
                        )

                        await open_trade(
                            connection,
                            side,
                            zone,
                            atr,
                            state
                        )

                await asyncio.sleep(LOOP_SECONDS)

            except (
                asyncio.TimeoutError,
                TimeoutError,
                ConnectionError
            ) as e:

                raise RuntimeError(
                    "METAAPI RECONNECT REQUIRED"
                ) from e

            except Exception as e:

                print(
                    "LOOP ERROR:",
                    type(e).__name__,
                    str(e),
                    flush=True
                )

                # If broker RPC is no longer responsive,
                # rebuild the entire session.

                message = str(e).lower()

                if any(
                    word in message
                    for word in (
                        "not connected",
                        "not synchronized",
                        "websocket",
                        "timed out",
                        "timeout",
                        "subscription",
                        "disconnected"
                    )
                ):

                    raise RuntimeError(
                        "METAAPI RECONNECT REQUIRED"
                    ) from e

                await asyncio.sleep(LOOP_SECONDS)

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
        "RIOBOT GOLD ZONES M1 V3 START\n"
        "SUPPORT / RESISTANCE / ATR14\n"
        f"LOT: {LOT_SIZE}\n"
        f"MAX TRADES: {MAX_TRADES}\n"
        f"COUNT: {state['trade_count']}\n"
        f"LIVE TRADING: {ENABLE_TRADING}\n"
        "TP1 + BE + TRAILING ACTIVE"
    )

    reconnect_attempt = 0

    while True:

        try:

            await bot_session(state)

            reconnect_attempt = 0

        except Exception as e:

            reconnect_attempt += 1

            delay = min(
                RECONNECT_SECONDS * reconnect_attempt,
                60
            )

            notify(
                "RIOBOT CONNECTION ERROR\n"
                f"{type(e).__name__}: {str(e)[:180]}\n"
                f"RECONNECT IN {delay} SECONDS"
            )

            await asyncio.sleep(delay)


# =====================================================
# START
# =====================================================

if __name__ == "__main__":

    asyncio.run(main())
