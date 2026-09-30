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
# RIOBOT GOLD V11 - RECONNECT FIX
# M1 + SUPPORT/RESISTANCE + ATR14
# 4 OBCHODY NA JEDEN SIGNAL
# BE ONLY - HOLD UNTIL BE OR TP
#
# FIX:
# - existing positions are adopted after deploy/reconnect
# - UNKNOWN POSITIONS problem fixed
# - no duplicate entries while positions exist
# - BE continues after reconnect
# =====================================================

SYMBOL = "XAUUSD"
COMMENT = "RIO GOLD V11 M1 ZONES ATR BE"

LOT_SIZE = 0.01
BATCH_SIZE = 4
MAX_TRADES = 4

ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "false")
    .strip().lower() == "true"
)

LOOP_SECONDS = 2

# reconnect po 5 sekundach
RECONNECT_SECONDS = 5

RPC_TIMEOUT = 20
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
MAX_ZONE_DISTANCE_ATR = 2.20

MAX_SPREAD = 0.40


# =====================================================
# SL / TP
# =====================================================

SL_ATR_BUFFER = 1.20

MIN_SL_DISTANCE = 2.50
MAX_SL_DISTANCE = 8.00

TP_RR = 1.50


# =====================================================
# BREAK EVEN ONLY
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
    "rio_gold_v11.json"
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
    return "RIOBOT GOLD V11 ACTIVE", 200


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

        os.fsync(
            f.fileno()
        )

    os.replace(
        temp,
        STATE_FILE
    )


def load_state():

    state = default_state()

    if not os.path.exists(STATE_FILE):

        print(
            "NEW V11 STATE",
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

        # do not blindly trade after corrupted state
        state["halted"] = True

    return state


# =====================================================
# METAAPI
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

    result = await meta_call(
        connection.get_positions()
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
# NEW FIX - ADOPT EXISTING POSITIONS
# =====================================================

async def adopt_existing_positions(
    connection,
    state
):

    """
    After Render deploy/restart/reconnect the /tmp state
    can be missing or incomplete.

    Existing XAUUSD positions are reconstructed from
    broker data so BE protection can continue.

    Safety:
    - max 4 positions
    - all must have known BUY/SELL side
    - all must have SL
    - risk reconstructed from openPrice -> stopLoss
    - no new orders are created here
    """

    positions = await get_positions(
        connection
    )

    if not positions:

        # No live positions.
        # Remove stale stored position IDs.
        if state["positions"]:

            state["positions"] = {}

            save_state(state)

        return positions

    if len(positions) > MAX_TRADES:

        state["halted"] = True

        save_state(state)

        notify(
            "RIO V11 SAFETY HALT\n"
            f"FOUND {len(positions)} XAUUSD POSITIONS\n"
            f"MAX EXPECTED: {MAX_TRADES}\n"
            "CHECK MT5"
        )

        return positions

    reconstructed = {}

    for position in positions:

        pid = str(
            position.get("id")
        )

        side = position_side(
            position
        )

        entry = float(
            position.get("openPrice")
            or 0
        )

        sl = float(
            position.get("stopLoss")
            or 0
        )

        volume = float(
            position.get("volume")
            or 0
        )

        if (
            not pid
            or side is None
            or entry <= 0
            or sl <= 0
        ):

            state["halted"] = True

            save_state(state)

            notify(
                "RIO V11 ADOPTION FAILED\n"
                f"POSITION: {pid}\n"
                "INVALID POSITION DATA\n"
                "CHECK MT5"
            )

            continue

        # Validate SL direction
        if side == "BUY":

            if sl >= entry:

                # Could already be BE/profit SL.
                # If old state knows original risk,
                # use it.
                old = state[
                    "positions"
                ].get(pid)

                if old and float(
                    old.get("risk", 0)
                ) > 0:

                    risk = float(
                        old["risk"]
                    )

                else:

                    # Cannot safely reconstruct original
                    # risk from a SL already above entry.
                    # Keep position monitored but don't
                    # invent risk.
                    reconstructed[pid] = {
                        "risk": 0,
                        "entry": entry,
                        "side": side
                    }

                    continue

            else:

                risk = entry - sl

        else:

            if sl <= entry:

                # Could already be BE/profit SL.
                old = state[
                    "positions"
                ].get(pid)

                if old and float(
                    old.get("risk", 0)
                ) > 0:

                    risk = float(
                        old["risk"]
                    )

                else:

                    reconstructed[pid] = {
                        "risk": 0,
                        "entry": entry,
                        "side": side
                    }

                    continue

            else:

                risk = sl - entry

        if risk <= 0:

            reconstructed[pid] = {
                "risk": 0,
                "entry": entry,
                "side": side
            }

            continue

        reconstructed[pid] = {
            "risk": risk,
            "entry": entry,
            "side": side
        }

        print(
            "ADOPTED POSITION:",
            pid,
            side,
            "VOL:",
            volume,
            "ENTRY:",
            entry,
            "SL:",
            sl,
            "RISK:",
            risk,
            flush=True
        )

    state["positions"] = reconstructed

    # Existing positions mean this batch has already
    # consumed at least this many orders.
    state["trade_count"] = max(
        state["trade_count"],
        len(positions)
    )

    state["last_trade_time"] = time.time()

    # Existing broker positions are now known.
    state["order_uncertain"] = False

    save_state(state)

    notify(
        "RIO V11 POSITIONS ADOPTED\n"
        f"OPEN: {len(positions)}/4\n"
        f"TRACKED: {len(reconstructed)}/4\n"
        "BE PROTECTION RESTORED"
    )

    return positions


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
# SUPPORT / RESISTANCE
# =====================================================

def detect_zones(
    candles,
    atr
):

    history = candles[
        -(ZONE_LOOKBACK + 3):-3
    ]

    if len(history) < ZONE_LOOKBACK:

        return (
            None,
            None,
            None
        )

    support = min(
        c["low"]
        for c in history
    )

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
# M1 SIGNAL
# =====================================================

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

    body_b = (
        abs(
            b["close"] - b["open"]
        )
        / range_b
    )

    body_c = (
        abs(
            c["close"] - c["open"]
        )
        / range_c
    )

    # BUY
    buy_zone_touch = (
        support is not None
        and (
            a["low"] <= support + tolerance
            or
            b["low"] <= support + tolerance
        )
        and (
            a["high"] >= support - tolerance
            or
            b["high"] >= support - tolerance
        )
    )

    buy_confirmation = (
        c["close"] > c["open"]
        and body_c >= MIN_BODY_RATIO
        and c["close"] > b["close"]
        and c["close"] > support
    )

    if (
        buy_zone_touch
        and buy_confirmation
    ):

        distance = (
            c["close"] - support
        )

        if (
            distance >= 0
            and distance
            <= atr * MAX_ZONE_DISTANCE_ATR
        ):

            return (
                "BUY",
                support,
                "M1 SUPPORT + ATR BUY"
            )

    # SELL
    sell_zone_touch = (
        resistance is not None
        and (
            a["high"] >= resistance - tolerance
            or
            b["high"] >= resistance - tolerance
        )
        and (
            a["low"] <= resistance + tolerance
            or
            b["low"] <= resistance + tolerance
        )
    )

    sell_confirmation = (
        c["close"] < c["open"]
        and body_c >= MIN_BODY_RATIO
        and c["close"] < b["close"]
        and c["close"] < resistance
    )

    if (
        sell_zone_touch
        and sell_confirmation
    ):

        distance = (
            resistance - c["close"]
        )

        if (
            distance >= 0
            and distance
            <= atr * MAX_ZONE_DISTANCE_ATR
        ):

            return (
                "SELL",
                resistance,
                "M1 RESISTANCE + ATR SELL"
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
            event.get(
                "impact",
                ""
            )
        ).lower()

        if currency != "USD":
            continue

        if (
            "high" not in impact
            and
            "red" not in impact
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
        or
        10 ** (-digits)
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


def normalize(
    value,
    market
):

    tick = market["tick"]

    return round(
        round(
            value / tick
        ) * tick,
        market["digits"]
    )


# =====================================================
# SL + TP
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

    if (
        side == "BUY"
        and sl >= entry
    ):

        return (
            None,
            "INVALID BUY SL"
        )

    if (
        side == "SELL"
        and sl <= entry
    ):

        return (
            None,
            "INVALID SELL SL"
        )

    return {
        "sl": sl,
        "tp": tp,
        "risk": actual_risk
    }, None


# =====================================================
# VERIFY NEW POSITION
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
# 4 ORDERS ON ONE SIGNAL
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
        or state["trade_count"]
        >= MAX_TRADES
    ):
        return

    positions = await get_positions(
        connection
    )

    if positions:

        print(
            "ENTRY BLOCKED: POSITION EXISTS",
            flush=True
        )

        return

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
                LOT_SIZE
                / volume_step
            )
        ) > 1e-7
    ):

        notify(
            "V11 INVALID LOT"
        )

        return

    entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )

    if (
        abs(
            entry
            - signal_close
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
        or
        10 ** (
            -market["digits"]
        )
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
            "RIO V11 TEST SIGNAL\n"
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
        "RIO V11 BATCH START\n"
        f"SIDE: {side}\n"
        f"TARGET: {BATCH_SIZE} POSITIONS\n"
        f"LOT EACH: {LOT_SIZE}\n"
        f"ATR: {atr:.2f}\n"
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

            if (
                state["trade_count"]
                >= MAX_TRADES
            ):
                break

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
                "RESPONSE:",
                result,
                flush=True
            )

            position = (
                await verify_new_position(
                    connection,
                    previous_ids,
                    side
                )
            )

            pid = str(
                position["id"]
            )

            actual_entry = float(
                position.get(
                    "openPrice"
                )
                or entry
            )

            actual_sl = float(
                position.get(
                    "stopLoss"
                )
                or 0
            )

            actual_tp = float(
                position.get(
                    "takeProfit"
                )
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

            if actual_risk <= 0:

                raise RuntimeError(
                    "INVALID ACTUAL RISK"
                )

            state["positions"][pid] = {
                "risk": actual_risk,
                "entry": actual_entry,
                "side": side
            }

            state["trade_count"] += 1
            state["last_trade_time"] = time.time()

            previous_ids.add(pid)

            save_state(state)

            notify(
                f"RIO V11 ORDER {number}/4 OK\n"
                f"SIDE: {side}\n"
                f"LOT: {LOT_SIZE}\n"
                f"ENTRY: {actual_entry}\n"
                f"SL: {actual_sl}\n"
                f"TP: {actual_tp}\n"
                f"POSITION: {pid}"
            )

        state["order_uncertain"] = False

        save_state(state)

        notify(
            "RIO V11 BATCH COMPLETED\n"
            f"CONFIRMED: "
            f"{state['trade_count']}/4\n"
            "BE ONLY ACTIVE"
        )

    except Exception as e:

        state["halted"] = True
        state["order_uncertain"] = True

        save_state(state)

        notify(
            "RIO V11 BATCH STOPPED\n"
            f"CONFIRMED: "
            f"{state['trade_count']}/4\n"
            f"ERROR: "
            f"{type(e).__name__}\n"
            f"{str(e)[:100]}\n"
            "CHECK MT5 - NO DUPLICATE ORDERS"
        )


# =====================================================
# BREAK EVEN ONLY
# =====================================================

async def protect_position(
    connection,
    position,
    state,
    market,
    atr
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
        info.get(
            "risk",
            0
        )
    )

    # risk 0 means position was already at BE when
    # reconstructed. Do not invent a new SL.
    if risk <= 0:
        return

    entry = float(
        position["openPrice"]
    )

    current_sl = float(
        position.get(
            "stopLoss"
        )
        or 0
    )

    current_tp = (
        position.get(
            "takeProfit"
        )
    )

    if current_sl <= 0:

        state["halted"] = True

        save_state(state)

        notify(
            f"CRITICAL POSITION {pid}\n"
            "BROKER SL MISSING\n"
            "CHECK MT5"
        )

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

    # Never move SL backwards
    if side == "BUY":

        if (
            wanted_sl
            <= current_sl
            + tick / 2
        ):
            return

    else:

        if (
            wanted_sl
            >= current_sl
            - tick / 2
        ):
            return

    spec = market["spec"]

    point = float(
        spec.get("point")
        or
        10 ** (
            -market["digits"]
        )
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
        "RIO V11 BREAK EVEN ACTIVE\n"
        f"POSITION: {pid}\n"
        f"SIDE: {side}\n"
        f"ENTRY: {entry}\n"
        f"BE SL: {wanted_sl}\n"
        f"TP: {current_tp}"
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

    print(
        "METAAPI REGION:",
        region,
        flush=True
    )

    if (
        str(
            account.state
        ).upper()
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

        # =============================================
        # IMPORTANT RECONNECT FIX
        # =============================================

        positions = await adopt_existing_positions(
            connection,
            state
        )

        notify(
            "RIO GOLD V11 CONNECTED\n"
            "POSITIONS VERIFIED\n"
            f"OPEN: {len(positions)}/4\n"
            f"COUNT: "
            f"{state['trade_count']}/4\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        last_atr = None
        last_atr_time = 0

        while True:

            try:

                # =====================================
                # POSITIONS FIRST
                # =====================================

                positions = await get_positions(
                    connection
                )

                current_ids = {
                    str(p["id"])
                    for p in positions
                }

                known_ids = set(
                    state[
                        "positions"
                    ].keys()
                )

                unknown_ids = (
                    current_ids
                    - known_ids
                )

                # Instead of endless UNKNOWN POSITIONS,
                # reconstruct them safely.
                if unknown_ids:

                    print(
                        "NEW/UNKNOWN POSITION IDS:",
                        unknown_ids,
                        flush=True
                    )

                    positions = (
                        await adopt_existing_positions(
                            connection,
                            state
                        )
                    )

                    current_ids = {
                        str(p["id"])
                        for p in positions
                    }

                    known_ids = set(
                        state[
                            "positions"
                        ].keys()
                    )

                closed_ids = (
                    known_ids
                    - current_ids
                )

                for pid in closed_ids:

                    state[
                        "positions"
                    ].pop(
                        pid,
                        None
                    )

                    state[
                        "last_trade_time"
                    ] = time.time()

                    save_state(state)

                    notify(
                        "RIO V11 POSITION CLOSED\n"
                        f"ID: {pid}\n"
                        f"REMAINING: "
                        f"{len(state['positions'])}"
                    )


                # =====================================
                # PROTECT OPEN POSITIONS - BE ONLY
                # =====================================

                if positions:

                    if (
                        time.time()
                        - last_atr_time
                        >= 60
                    ):

                        try:

                            candles = (
                                await get_candles(
                                    region
                                )
                            )

                            last_atr = (
                                calculate_atr(
                                    candles
                                )
                            )

                            last_atr_time = (
                                time.time()
                            )

                        except Exception as e:

                            print(
                                "ATR ERROR:",
                                e,
                                flush=True
                            )

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

                        try:

                            await protect_position(
                                connection,
                                position,
                                state,
                                market,
                                last_atr
                            )

                        except Exception as e:

                            print(
                                "PROTECTION ERROR:",
                                pid,
                                e,
                                flush=True
                            )

                            message = str(
                                e
                            ).lower()

                            if (
                                isinstance(
                                    e,
                                    asyncio.TimeoutError
                                )
                                or
                                "connected"
                                in message
                                or
                                "timeout"
                                in message
                                or
                                "websocket"
                                in message
                                or
                                "synchronized"
                                in message
                            ):

                                raise

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # NEW ENTRY SAFETY
                # =====================================

                if (
                    state["halted"]
                    or
                    state[
                        "order_uncertain"
                    ]
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
                        "RIO V11 LIMIT REACHED\n"
                        "4/4 ORDERS SENT\n"
                        "NEW ENTRIES STOPPED"
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                if (
                    time.time()
                    - state[
                        "last_trade_time"
                    ]
                    < COOLDOWN_SECONDS
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # NEWS
                # =====================================

                if await news_blocked():

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # M1 + ZONES + ATR14
                # =====================================

                candles = await get_candles(
                    region
                )

                candle_time = (
                    candles[-1]["time"]
                )

                if (
                    candle_time
                    == state[
                        "last_candle"
                    ]
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
                    round(
                        atr,
                        2
                    ),
                    "\nRESULT:",
                    side or reason,
                    flush=True
                )

                if side is not None:

                    signal_key = (
                        f"{candle_time}:"
                        f"{side}"
                    )

                    if (
                        signal_key
                        != state[
                            "last_signal"
                        ]
                    ):

                        state[
                            "last_signal"
                        ] = signal_key

                        save_state(state)

                        notify(
                            "RIO V11 SIGNAL\n"
                            f"SIDE: {side}\n"
                            f"REASON: {reason}\n"
                            f"ZONE: {zone:.2f}\n"
                            f"ATR: {atr:.2f}\n"
                            "REQUEST: 4 POSITIONS"
                        )

                        await open_batch(
                            connection,
                            side,
                            zone,
                            atr,
                            candles[-1][
                                "close"
                            ],
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
        "RIOBOT GOLD V11 START\n"
        "M1 + SUPPORT/RESISTANCE + ATR14\n"
        "NO M5 FILTER\n"
        "4 POSITIONS ON ONE SIGNAL\n"
        "ATR SL + 1.5R TP\n"
        "BE ONLY - HOLD UNTIL BE OR TP\n"
        "RECONNECT POSITION RECOVERY ACTIVE\n"
        "METAAPI 5 SEC RECONNECT\n"
        f"LOT EACH: {LOT_SIZE}\n"
        f"BATCH: {BATCH_SIZE}\n"
        f"COUNT: "
        f"{state['trade_count']}/4\n"
        f"LIVE: {ENABLE_TRADING}\n"
        f"SAFETY HALT: "
        f"{state['halted']}"
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
                "RIO V11 CONNECTION ERROR\n"
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
