
import os
import json
import time
import math
import asyncio
import traceback
import requests

from datetime import datetime, timezone
from threading import Thread
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =====================================================
# RIOBOT GOLD V7 - M1 + M5
# =====================================================

SYMBOL = "XAUUSD"
COMMENT = "RIO GOLD V7"

LOT_SIZE = 0.01
MAX_TRADES = 4

ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "false")
    .strip().lower() == "true"
)

LOOP_SECONDS = 3
RECONNECT_SECONDS = 5

RPC_TIMEOUT = 20
CONNECT_TIMEOUT = 90

COOLDOWN_SECONDS = 180

ZONE_LOOKBACK = 35
ATR_PERIOD = 14

M5_EMA_PERIOD = 5

ZONE_ATR_TOLERANCE = 0.35
MIN_ZONE_TOLERANCE = 0.35

MIN_BODY_RATIO = 0.30
MAX_SIGNAL_RANGE_ATR = 1.80

MAX_ENTRY_DRIFT_ATR = 0.35
MAX_ZONE_DISTANCE_ATR = 1.80

MAX_SPREAD = 0.40

# Sirsi nudzovy SL podla volatility.
SL_ATR_BUFFER = 0.60

MIN_SL_DISTANCE = 1.00
MAX_SL_DISTANCE = 7.00

# Skorsi BE.
BE_TRIGGER_RR = 0.40
BE_LOCK_DISTANCE = 0.10

TP1_RR = 0.80
TP1_LOCK_RR = 0.35

TRAIL_TRIGGER_RR = 1.30
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
    "rio_gold_v7.json"
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
    return "RIOBOT GOLD V7 ACTIVE", 200


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
        "position_id": None,
        "trade_risk": None,
        "last_trade_time": 0,
        "last_candle": None,
        "last_signal": None,
        "had_position": False,
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
            "NEW STATE CREATED. "
            "TRADE COUNTER STARTS AT ZERO.",
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
            str(e),
            flush=True
        )

        state["halted"] = True

    return state


# =====================================================
# METAAPI CALL
# =====================================================

async def meta_call(coroutine, timeout=RPC_TIMEOUT):

    return await asyncio.wait_for(
        coroutine,
        timeout=timeout
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
        "?limit=180"
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
        timeout=16
    )

    if not isinstance(raw, list):

        raise RuntimeError(
            "INVALID CANDLE RESPONSE"
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

            dt = dt.astimezone(
                timezone.utc
            )

            age = (
                now - dt
            ).total_seconds()

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

    candles = sorted(
        {
            c["time"]: c
            for c in candles
        }.values(),
        key=lambda c: c["time"]
    )

    if len(candles) < 70:

        raise RuntimeError(
            "NOT ENOUGH M1 HISTORY"
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
# ATR
# =====================================================

def calculate_atr(candles):

    if len(candles) < ATR_PERIOD + 2:

        return None

    ranges = []

    for i in range(1, len(candles)):

        c = candles[i]
        p = candles[i - 1]

        tr = max(
            c["high"] - c["low"],
            abs(
                c["high"] - p["close"]
            ),
            abs(
                c["low"] - p["close"]
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
            timestamp
            - timestamp % 300
        )

        groups.setdefault(
            bucket, []
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
        sum(values[:period])
        / period
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

        return "NONE"

    closes = [
        c["close"] for c in m5
    ]

    emas = ema_values(
        closes,
        M5_EMA_PERIOD
    )

    if len(emas) < 3:

        return "NONE"

    last = m5[-1]
    previous = m5[-2]

    ema_now = emas[-1]
    ema_previous = emas[-2]

    bullish = (
        last["close"] > last["open"]
        and last["close"] > previous["close"]
        and last["close"] > ema_now
        and ema_now > ema_previous
    )

    bearish = (
        last["close"] < last["open"]
        and last["close"] < previous["close"]
        and last["close"] < ema_now
        and ema_now < ema_previous
    )

    if bullish:

        return "BUY"

    if bearish:

        return "SELL"

    return "NONE"


# =====================================================
# ZONES + SIGNAL
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

    return (
        support,
        resistance,
        tolerance
    )


def get_signal(
    candles,
    support,
    resistance,
    tolerance,
    atr
):

    a = candles[-3]
    b = candles[-2]
    c = candles[-1]

    range_b = (
        b["high"] - b["low"]
    )

    range_c = (
        c["high"] - c["low"]
    )

    if range_b <= 0 or range_c <= 0:

        return None, None, "ZERO RANGE"

    if (
        range_b > atr * MAX_SIGNAL_RANGE_ATR
        or range_c > atr * MAX_SIGNAL_RANGE_ATR
    ):

        return None, None, "IMPULSE"

    body_b = (
        abs(b["close"] - b["open"])
        / range_b
    )

    body_c = (
        abs(c["close"] - c["open"])
        / range_c
    )

    bullish = (
        b["close"] > b["open"]
        and c["close"] > c["open"]
        and body_b >= MIN_BODY_RATIO
        and body_c >= MIN_BODY_RATIO
        and c["close"] > b["high"]
        and b["low"] >= a["low"] - tolerance
    )

    bearish = (
        b["close"] < b["open"]
        and c["close"] < c["open"]
        and body_b >= MIN_BODY_RATIO
        and body_c >= MIN_BODY_RATIO
        and c["close"] < b["low"]
        and b["high"] <= a["high"] + tolerance
    )

    buy_touch = (
        support is not None
        and a["low"] <= support + tolerance
        and a["high"] >= support - tolerance
    )

    sell_touch = (
        resistance is not None
        and a["high"] >= resistance - tolerance
        and a["low"] <= resistance + tolerance
    )

    trend = m5_trend(candles)

    if buy_touch and bullish:

        if trend != "BUY":

            return (
                None,
                None,
                "BUY BLOCKED BY M5"
            )

        if c["close"] <= support:

            return None, None, "BELOW SUPPORT"

        if (
            c["close"] - support
            > atr * MAX_ZONE_DISTANCE_ATR
        ):

            return None, None, "TOO FAR"

        return (
            "BUY",
            support,
            "M1 + M5 BUY"
        )

    if sell_touch and bearish:

        if trend != "SELL":

            return (
                None,
                None,
                "SELL BLOCKED BY M5"
            )

        if c["close"] >= resistance:

            return None, None, "ABOVE RESISTANCE"

        if (
            resistance - c["close"]
            > atr * MAX_ZONE_DISTANCE_ATR
        ):

            return None, None, "TOO FAR"

        return (
            "SELL",
            resistance,
            "M1 + M5 SELL"
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

            news_cache["updated"] = (
                time.time()
            )

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
        connection.get_positions()
    )

    if not isinstance(positions, list):

        raise RuntimeError(
            "INVALID POSITION RESPONSE"
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
# SL
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

    sl = normalize(
        sl,
        market
    )

    risk = abs(entry - sl)

    if risk < MIN_SL_DISTANCE:

        return None, "SL TOO SMALL"

    if risk > MAX_SL_DISTANCE:

        return None, "SL TOO LARGE"

    if side == "BUY" and sl >= entry:

        return None, "INVALID BUY SL"

    if side == "SELL" and sl <= entry:

        return None, "INVALID SELL SL"

    return {
        "sl": sl,
        "risk": risk
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

    # Pred vstupom MUSIME poznat skutocne pozicie.
    positions = await get_positions(connection)

    if positions:

        print(
            "ENTRY BLOCKED: POSITION EXISTS",
            flush=True
        )

        return False

    market = await get_market(connection)

    spread = (
        market["ask"] - market["bid"]
    )

    if spread > MAX_SPREAD:

        print(
            "ENTRY BLOCKED: SPREAD",
            spread,
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

        notify(
            "ENTRY BLOCKED: INVALID LOT"
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

        return False

    zone_distance = (
        entry - zone
        if side == "BUY"
        else zone - entry
    )

    if (
        zone_distance < 0
        or zone_distance
        > atr * MAX_ZONE_DISTANCE_ATR
    ):

        return False

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

        return False

    sl = levels["sl"]

    point = float(
        spec.get("point")
        or 10 ** (-market["digits"])
    )

    stops = float(
        spec.get("stopsLevel") or 0
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

            return False

    else:

        if (
            sl - market["ask"]
            <= min_distance
        ):

            return False

    if not ENABLE_TRADING:

        notify(
            "TEST SIGNAL V7\n"
            f"SIDE: {side}\n"
            f"ENTRY: {entry}\n"
            f"SL: {sl}"
        )

        return False

    # Ak odpoved na objednavku vypadne,
    # nikdy neposielame automaticky druhy vstup.

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
                ),
                timeout=35
            )

        else:

            result = await meta_call(
                connection.create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    None,
                    options
                ),
                timeout=35
            )

        print(
            "ORDER RESPONSE:",
            result,
            flush=True
        )

        # Broker potrebuje chvilu na aktualizaciu.
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
                "CRITICAL: POSITION WITHOUT SL\n"
                "CHECK MT5"
            )

            return False

        actual_entry = float(
            position.get("openPrice")
            or entry
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

        state["order_uncertain"] = False

        state["last_trade_time"] = (
            time.time()
        )

        save_state(state)

        notify(
            "RIO V7 ORDER OK\n"
            f"SIDE: {side}\n"
            f"LOT: {LOT_SIZE}\n"
            f"ENTRY: {actual_entry}\n"
            f"SL: {actual_sl}\n"
            f"RISK DISTANCE: {actual_risk:.2f}\n"
            f"TRADE: {state['trade_count']}/{MAX_TRADES}"
        )

        return True

    except Exception as e:

        state["halted"] = True

        save_state(state)

        notify(
            "ORDER STATUS UNCERTAIN\n"
            f"{type(e).__name__}: {str(e)[:120]}\n"
            "CHECK MT5\n"
            "NEW ENTRIES STOPPED"
        )

        return False


# =====================================================
# EARLY BE + TP1 LOCK + TRAILING
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
            "CRITICAL: NO BROKER SL\n"
            "CHECK MT5"
        )

        return

    direction = (
        1 if side == "BUY"
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

    wanted_sl = current_sl

    stage = None

    # BE uz pri +0.40R.

    if (
        profit_distance
        >= risk * BE_TRIGGER_RR
    ):

        be = (
            entry
            + direction * BE_LOCK_DISTANCE
        )

        wanted_sl = (
            max(wanted_sl, be)
            if side == "BUY"
            else min(wanted_sl, be)
        )

        stage = "EARLY BE"

    # Virtualne TP1, posun SL.

    if (
        profit_distance
        >= risk * TP1_RR
    ):

        lock = (
            entry
            + direction
            * risk
            * TP1_LOCK_RR
        )

        wanted_sl = (
            max(wanted_sl, lock)
            if side == "BUY"
            else min(wanted_sl, lock)
        )

        stage = "TP1 LOCK"

    # Trailing.

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

        wanted_sl = (
            max(wanted_sl, trail)
            if side == "BUY"
            else min(wanted_sl, trail)
        )

        stage = "TRAILING"

    wanted_sl = normalize(
        wanted_sl,
        market
    )

    tick = market["tick"]

    # Nikdy nevracat SL naspat.

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

    min_distance = (
        max(stops, freeze) * point
        + tick
    )

    if side == "BUY":

        if (
            wanted_sl
            >= market["bid"] - min_distance
        ):

            return

    else:

        if (
            wanted_sl
            <= market["ask"] + min_distance
        ):

            return

    await meta_call(
        connection.modify_position(
            position["id"],
            wanted_sl,
            position.get("takeProfit")
        ),
        timeout=25
    )

    notify(
        "RIO V7 SL UPDATED\n"
        f"SIDE: {side}\n"
        f"STAGE: {stage}\n"
        f"SL: {wanted_sl}"
    )


# =====================================================
# CONNECTION SESSION
# =====================================================

async def bot_session(state):

    # Pre Python SDK region NEPOSIELAME
    # do konstruktora MetaApi.

    api = MetaApi(M_TOKEN)

    account = await meta_call(
        api.metatrader_account_api.get_account(
            M_ACC
        ),
        timeout=35
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

    if str(account.state).upper() != "DEPLOYED":

        await meta_call(
            account.deploy(),
            timeout=45
        )

    await asyncio.wait_for(
        account.wait_connected(),
        timeout=CONNECT_TIMEOUT
    )

    connection = account.get_rpc_connection()

    try:

        await meta_call(
            connection.connect(),
            timeout=CONNECT_TIMEOUT
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=CONNECT_TIMEOUT
        )

        # CONNECTED oznamime az po realnej
        # odpovedi na GET POSITIONS.

        positions = await get_positions(
            connection
        )

        notify(
            "RIO GOLD V7 CONNECTED\n"
            "POSITIONS VERIFIED\n"
            f"LOT: {LOT_SIZE}\n"
            f"TRADES: {state['trade_count']}/{MAX_TRADES}\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        # Ziadne automaticke resetovanie
        # neistej objednavky.

        if state["order_uncertain"]:

            state["halted"] = True

            save_state(state)

            notify(
                "UNCERTAIN PREVIOUS ORDER\n"
                "CHECK MT5"
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

        last_atr = None
        last_atr_time = 0

        while True:

            try:

                # 1. Pozicie kontrolujeme vzdy prve.

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

                        state["halted"] = True

                        save_state(state)

                        await asyncio.sleep(
                            LOOP_SECONDS
                        )

                        continue

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

                            last_atr_time = (
                                time.time()
                            )

                        except Exception as e:

                            print(
                                "ATR ERROR:",
                                str(e),
                                flush=True
                            )

                    # BE kontrolujeme aj ked
                    # su nove vstupy zastavene.

                    await protect_position(
                        connection,
                        positions[0],
                        state,
                        last_atr
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # 2. Pozicia sa zatvorila.

                if state["had_position"]:

                    state["had_position"] = False

                    state["position_id"] = None

                    state["trade_risk"] = None

                    state["last_trade_time"] = (
                        time.time()
                    )

                    save_state(state)

                    notify(
                        "RIO POSITION CLOSED\n"
                        "COOLDOWN 180 SECONDS"
                    )

                # 3. Kontrola novych vstupov.

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

                # 4. News filter.

                if await news_blocked():

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # 5. M1 signal.

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

                trend = m5_trend(
                    candles
                )

                print(
                    "\nM1 CHECK:",
                    candle_time,
                    "\nCLOSE:",
                    candles[-1]["close"],
                    "\nSUPPORT:",
                    support,
                    "\nRESISTANCE:",
                    resistance,
                    "\nATR:",
                    round(atr, 2),
                    "\nM5 TREND:",
                    trend,
                    "\nRESULT:",
                    side or reason,
                    flush=True
                )

                # 6. Potvrdeny vstup.

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
                            "RIO V7 SIGNAL\n"
                            f"SIDE: {side}\n"
                            f"REASON: {reason}\n"
                            f"ZONE: {zone:.2f}\n"
                            f"ATR: {atr:.2f}"
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

            except Exception as e:

                message = str(e).lower()

                print(
                    "LOOP ERROR:",
                    type(e).__name__,
                    str(e),
                    flush=True
                )

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
                            "not connected",
                            "not synchronized",
                            "websocket",
                            "timed out",
                            "timeout",
                            "subscription",
                            "disconnected",
                            "broker yet"
                        )
                    )
                )

                if connection_error:

                    raise RuntimeError(
                        "METAAPI CONNECTION LOST"
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
        "RIOBOT GOLD V7 START\n"
        "M1 + M5 TREND\n"
        "WIDER ATR SL\n"
        "EARLY BE + TP1 LOCK + TRAILING\n"
        "METAAPI CONNECTION CHECK\n"
        f"LOT: {LOT_SIZE}\n"
        f"MAX TRADES: {MAX_TRADES}\n"
        f"COUNT: {state['trade_count']}\n"
        f"LIVE: {ENABLE_TRADING}\n"
        f"SAFETY HALT: {state['halted']}"
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
                30
            )

            print(
                "SESSION ERROR:",
                traceback.format_exc(),
                flush=True
            )

            notify(
                "RIO V7 CONNECTION ERROR\n"
                f"{type(e).__name__}: {str(e)[:120]}\n"
                f"RECONNECT IN {delay} SECONDS"
            )

            await asyncio.sleep(
                delay
            )


# =====================================================
# START
# =====================================================

if __name__ == "__main__":

    asyncio.run(main())
