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
# RIOBOT GOLD V14.4
# STRONG M5 REVERSAL OVERRIDE / STRICT SCALP
#
# V14.4:
# - M15 ostava hlavny trend filter
# - M15 neutral -> M5 moze urcit smer
# - NOVINKA:
#   silny M5 reversal moze prebit stary M15 smer
# - na M5 reversal nestaci obycajna sviecka:
#   * EMA9/20 potvrdenie
#   * EMA9 slope
#   * strong body
#   * break previous M5 high/low
# - po M5 reversal je stale POVINNY M1 retest
#   + M1 momentum
#
# ZACHOVANE:
# - FAST 12-position batch
# - 12 x 0.10 lot
# - NO CHASE pred batchom
# - spolocny SL
# - TP1/TP2/TP3/TP4
# - BE1/BE2/BE3
# - 180 s cooldown
# - fresh setup lock
# - same-direction reentry protection
# =====================================================

VERSION = "V14.4 STRONG M5 REVERSAL"

SYMBOL = "XAUUSD"

# Zachovame V14 prefix, aby nova verzia vedela
# adoptovat otvorene pozicie z V14.x.
COMMENT_PREFIX = "RIOGOLDV14"


# =====================================================
# BATCH
# =====================================================

LOT_SIZE = 0.10

BATCH_SIZE = 12
MAX_TRADES = 12

TP1_COUNT = 3
TP2_COUNT = 3
TP3_COUNT = 3
TP4_COUNT = 3

TP1_RR = 0.40
TP2_RR = 0.70
TP3_RR = 1.00
TP4_RR = 1.30

BATCH_VERIFY_ATTEMPTS = 16
BATCH_VERIFY_DELAY = 0.50


# =====================================================
# LIVE
# =====================================================

ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "true")
    .strip()
    .lower()
    == "true"
)

LOOP_SECONDS = 2

COOLDOWN_SECONDS = 180


# =====================================================
# METAAPI
# =====================================================

RECONNECT_SECONDS = 3

RPC_TIMEOUT = 25
RPC_RETRIES = 4
RPC_RETRY_DELAY = 2

CONNECT_TIMEOUT = 120
ORDER_TIMEOUT = 45

MAX_CONSECUTIVE_RPC_FAILURES = 8
RPC_FAILURE_WAIT = 3


# =====================================================
# DATA / FRESHNESS
# =====================================================

M1_HISTORY_LIMIT = 1000
MIN_M1_HISTORY = 400

MAX_ENTRY_CANDLE_AGE = 130
STALE_DATA_RETRY_SECONDS = 5

CURRENT_M1_REFRESH_SECONDS = 30
CURRENT_M1_TIMEOUT = 12

HISTORY_TIMEOUT = 20
HISTORY_WARMUP_WAIT = 1


# =====================================================
# INDICATORS
# =====================================================

ATR_PERIOD = 14

# M15
M15_FAST_EMA = 20
M15_SLOW_EMA = 50

# M5
M5_FAST_EMA = 9
M5_SLOW_EMA = 20

# M1
M1_FAST_EMA = 9
M1_SLOW_EMA = 20


# =====================================================
# M15 ADAPTIVE
# =====================================================

M15_ALLOW_TRANSITION = True
M15_ALLOW_M5_FALLBACK = True


# =====================================================
# V14.4 STRONG M5 REVERSAL OVERRIDE
# =====================================================

M5_REVERSAL_OVERRIDE_ENABLED = True

# Telo M5 reversal sviecky musi tvorit aspon
# 45 % celeho rozsahu sviecky.
M5_REVERSAL_MIN_BODY_RATIO = 0.45

# Strong BUY musi zavriet nad previous M5 HIGH.
# Strong SELL musi zavriet pod previous M5 LOW.
M5_REVERSAL_REQUIRE_BREAK = True


# =====================================================
# STRICT M1 SCALP ENTRY
# =====================================================

M1_RETEST_LOOKBACK = 3
M1_RETEST_TOLERANCE_ATR = 0.25

M1_MAX_EMA9_DISTANCE_ATR = 0.60

M1_MIN_BODY_RATIO = 0.35
M1_MIN_WICK_RATIO = 0.18

MAX_M1_RANGE_ATR = 1.80


# =====================================================
# ENTRY DRIFT / NO CHASE
# =====================================================

MAX_FORWARD_DRIFT_ATR = 0.40
MAX_ADVERSE_DRIFT_ATR = 0.30


# =====================================================
# SPREAD
# =====================================================

MAX_SPREAD = 0.50
MAX_SPREAD_ATR = 1.00


# =====================================================
# SL
# =====================================================

SWING_LEFT = 2
SWING_RIGHT = 2

SL_SWING_LOOKBACK = 16
SL_FALLBACK_BARS = 10

SL_SWING_BUFFER_ATR = 0.75

MIN_SL_DISTANCE = 1.80
MIN_SL_ATR_MULT = 1.40

MAX_SL_DISTANCE = 4.50
MAX_SL_ATR_MULT = 3.50


# =====================================================
# BREAK EVEN
# =====================================================

BE1_TRIGGER_RR = 0.30
BE1_LOCK_RR = 0.05

BE2_TRIGGER_RR = 0.60
BE2_LOCK_RR = 0.25

BE3_TRIGGER_RR = 0.90
BE3_LOCK_RR = 0.50


# =====================================================
# FRESH SETUP
# =====================================================

NEW_SETUP_RELEASE_ATR = 0.50


# =====================================================
# NEWS
# =====================================================

NEWS_FILTER_ENABLED = False


# =====================================================
# ENV
# =====================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

STATE_DIR = os.getenv(
    "RIO_STATE_DIR",
    "/tmp"
)

# Zachovame state z V14.
STATE_FILE = os.path.join(
    STATE_DIR,
    "rio_gold_v14.json"
)


# =====================================================
# CACHE
# =====================================================

market_data_cache = {
    "last_current_check": 0,
    "current_candle": None
}


# =====================================================
# ERROR
# =====================================================

class MetaApiTemporaryError(Exception):
    pass


# =====================================================
# RENDER
# =====================================================

app = Flask(__name__)


@app.route("/")
def home():

    return (
        "RIOBOT GOLD V14.4 STRONG M5 REVERSAL ACTIVE",
        200
    )


def keep_alive():

    def run():

        app.run(
            host="0.0.0.0",
            port=int(
                os.getenv(
                    "PORT",
                    "10000"
                )
            ),
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

    print(
        message,
        flush=True
    )

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
        "positions": {},
        "last_trade_time": 0,
        "last_candle": None,
        "last_signal": None,

        "order_uncertain": False,
        "halted": False,

        "locked_side": None,
        "locked_reference": None,
        "setup_released": True
    }


def save_state(state):

    os.makedirs(
        STATE_DIR,
        exist_ok=True
    )

    temp = STATE_FILE + ".tmp"

    with open(
        temp,
        "w"
    ) as f:

        json.dump(
            state,
            f
        )

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

    if not os.path.exists(
        STATE_FILE
    ):

        print(
            "NEW GOLD V14.4 STATE",
            flush=True
        )

        return state

    try:

        with open(
            STATE_FILE
        ) as f:

            saved = json.load(
                f
            )

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
# METAAPI SAFE CALL
# =====================================================

async def meta_call(
    coroutine_factory,
    timeout=RPC_TIMEOUT,
    retries=RPC_RETRIES
):

    last_error = None

    for attempt in range(
        1,
        retries + 1
    ):

        try:

            return await asyncio.wait_for(
                coroutine_factory(),
                timeout=timeout
            )

        except (
            asyncio.TimeoutError,
            TimeoutError
        ) as e:

            last_error = e

            print(
                "METAAPI RPC TIMEOUT "
                f"{attempt}/{retries}",
                flush=True
            )

        except Exception as e:

            last_error = e

            message = str(
                e
            ).lower()

            temporary = any(
                word in message
                for word in (
                    "timeout",
                    "timed out",
                    "not connected",
                    "not synchronized",
                    "websocket",
                    "subscription",
                    "disconnected",
                    "broker yet",
                    "connection lost",
                    "failed to subscribe"
                )
            )

            if not temporary:
                raise

            print(
                "METAAPI TEMP ERROR "
                f"{attempt}/{retries}: {e}",
                flush=True
            )

        if attempt < retries:

            await asyncio.sleep(
                RPC_RETRY_DELAY
            )

    raise MetaApiTemporaryError(
        "METAAPI TEMPORARILY UNAVAILABLE: "
        f"{last_error}"
    )


# =====================================================
# POSITION HELPERS
# =====================================================

def position_side(position):

    value = str(
        position.get(
            "type",
            ""
        )
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


def position_comment(position):

    return str(
        position.get(
            "comment",
            ""
        )
        or ""
    ).upper()


def is_managed_position(position):

    return position_comment(
        position
    ).startswith(
        COMMENT_PREFIX
    )


async def get_positions(connection):

    result = await meta_call(
        lambda:
        connection.get_positions()
    )

    if not isinstance(
        result,
        list
    ):

        raise RuntimeError(
            "INVALID POSITIONS RESPONSE"
        )

    return [
        position
        for position in result
        if str(
            position.get(
                "symbol",
                ""
            )
        ).upper() == SYMBOL
    ]


# =====================================================
# TIME
# =====================================================

def parse_api_time(value):

    dt = datetime.fromisoformat(
        str(value).replace(
            "Z",
            "+00:00"
        )
    )

    if dt.tzinfo is None:

        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def iso_z(dt):

    dt = dt.astimezone(
        timezone.utc
    )

    return (
        dt.strftime(
            "%Y-%m-%dT%H:%M:%S."
        )
        +
        f"{int(dt.microsecond / 1000):03d}Z"
    )


# =====================================================
# CURRENT M1
# =====================================================

async def get_current_m1(
    region,
    force=False
):

    now_ts = time.time()

    if (
        not force
        and
        market_data_cache[
            "current_candle"
        ] is not None
        and
        now_ts
        -
        market_data_cache[
            "last_current_check"
        ]
        <
        CURRENT_M1_REFRESH_SECONDS
    ):

        return market_data_cache[
            "current_candle"
        ]

    url = (
        "https://mt-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/"
        f"symbols/{SYMBOL}/"
        "current-candles/1m"
    )

    def fetch():

        response = requests.get(
            url,
            headers={
                "auth-token": M_TOKEN
            },
            params={
                "keepSubscription": "true"
            },
            timeout=CURRENT_M1_TIMEOUT
        )

        if response.status_code != 200:

            raise RuntimeError(
                "CURRENT M1 HTTP "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            )

        return response.json()

    try:

        current = await asyncio.wait_for(
            asyncio.to_thread(
                fetch
            ),
            timeout=CURRENT_M1_TIMEOUT + 3
        )

    except Exception as e:

        print(
            "CURRENT M1 WARMUP ERROR:",
            e,
            flush=True
        )

        return market_data_cache.get(
            "current_candle"
        )

    if not isinstance(
        current,
        dict
    ):

        print(
            "CURRENT M1 INVALID RESPONSE",
            flush=True
        )

        return market_data_cache.get(
            "current_candle"
        )

    try:

        candle_time = parse_api_time(
            current["time"]
        )

        age = (
            datetime.now(
                timezone.utc
            )
            -
            candle_time
        ).total_seconds()

        print(
            "GOLD V14.4 CURRENT M1:",
            candle_time.isoformat(),
            f"AGE={age:.0f}s",
            flush=True
        )

    except Exception as e:

        print(
            "CURRENT M1 TIME ERROR:",
            e,
            flush=True
        )

    market_data_cache[
        "current_candle"
    ] = current

    market_data_cache[
        "last_current_check"
    ] = now_ts

    return current


# =====================================================
# HISTORICAL M1
# =====================================================

async def fetch_historical_m1(
    region,
    start_time
):

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
            params={
                "startTime":
                    iso_z(
                        start_time
                    ),
                "limit":
                    M1_HISTORY_LIMIT
            },
            timeout=15
        )

        if response.status_code != 200:

            raise RuntimeError(
                "M1 HTTP "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            )

        return response.json()

    try:

        return await asyncio.wait_for(
            asyncio.to_thread(
                fetch
            ),
            timeout=HISTORY_TIMEOUT
        )

    except Exception as e:

        raise MetaApiTemporaryError(
            f"M1 DATA ERROR: {e}"
        ) from e


def parse_historical_candles(
    raw,
    now
):

    if not isinstance(
        raw,
        list
    ):

        raise RuntimeError(
            "INVALID M1 DATA"
        )

    candles = []

    for item in raw:

        try:

            dt = parse_api_time(
                item["time"]
            )

            # Pouzivame iba uzavrete M1.
            if (
                now - dt
            ).total_seconds() < 61:

                continue

            candles.append({
                "time":
                    dt.isoformat(),

                "open":
                    float(
                        item["open"]
                    ),

                "high":
                    float(
                        item["high"]
                    ),

                "low":
                    float(
                        item["low"]
                    ),

                "close":
                    float(
                        item["close"]
                    )
            })

        except (
            KeyError,
            TypeError,
            ValueError
        ):

            continue

    candles = sorted(
        {
            candle["time"]:
                candle
            for candle in candles
        }.values(),
        key=lambda candle:
            candle["time"]
    )

    return candles


# =====================================================
# FRESH M1
# =====================================================

async def get_candles(region):

    now = datetime.now(
        timezone.utc
    )

    current = await get_current_m1(
        region
    )

    history_start = (
        now
        +
        timedelta(
            minutes=1
        )
    )

    raw = await fetch_historical_m1(
        region,
        history_start
    )

    candles = parse_historical_candles(
        raw,
        now
    )

    if (
        len(candles)
        <
        MIN_M1_HISTORY
    ):

        raise MetaApiTemporaryError(
            "NOT ENOUGH M1 HISTORY: "
            f"{len(candles)}"
        )

    last_time = datetime.fromisoformat(
        candles[-1]["time"]
    )

    age_seconds = (
        now
        -
        last_time
    ).total_seconds()

    current_time = None
    current_age = None

    if isinstance(
        current,
        dict
    ):

        try:

            current_time = parse_api_time(
                current["time"]
            )

            current_age = (
                now
                -
                current_time
            ).total_seconds()

        except Exception:

            pass

    if (
        age_seconds
        >
        MAX_ENTRY_CANDLE_AGE
        and
        current_time is not None
        and
        current_age is not None
        and
        current_age < 180
    ):

        print(
            "GOLD V14.4 HISTORICAL M1 STALE "
            "BUT LIVE M1 FRESH",
            f"HIST_AGE={age_seconds:.0f}s",
            f"LIVE_AGE={current_age:.0f}s",
            flush=True
        )

        await get_current_m1(
            region,
            force=True
        )

        await asyncio.sleep(
            HISTORY_WARMUP_WAIT
        )

        retry_start = (
            current_time
            +
            timedelta(
                minutes=1
            )
        )

        raw = await fetch_historical_m1(
            region,
            retry_start
        )

        now = datetime.now(
            timezone.utc
        )

        retry_candles = (
            parse_historical_candles(
                raw,
                now
            )
        )

        if (
            len(retry_candles)
            >=
            MIN_M1_HISTORY
        ):

            candles = retry_candles

            last_time = (
                datetime.fromisoformat(
                    candles[-1]["time"]
                )
            )

            age_seconds = (
                now
                -
                last_time
            ).total_seconds()

    candles[-1][
        "age_seconds"
    ] = age_seconds

    print(
        "GOLD V14.4 M1 DATA:",
        candles[-1]["time"],
        f"AGE={age_seconds:.0f}s",
        f"COUNT={len(candles)}",
        flush=True
    )

    return candles


# =====================================================
# ATR
# =====================================================

def calculate_atr(candles):

    if (
        len(candles)
        <
        ATR_PERIOD + 2
    ):

        return None

    ranges = []

    for i in range(
        1,
        len(candles)
    ):

        candle = candles[i]
        previous = candles[
            i - 1
        ]

        true_range = max(
            candle["high"]
            -
            candle["low"],

            abs(
                candle["high"]
                -
                previous["close"]
            ),

            abs(
                candle["low"]
                -
                previous["close"]
            )
        )

        ranges.append(
            true_range
        )

    return (
        sum(
            ranges[
                -ATR_PERIOD:
            ]
        )
        /
        ATR_PERIOD
    )


# =====================================================
# BUILD M5 / M15
# =====================================================

def build_tf_candles(
    candles,
    minutes
):

    groups = {}

    for candle in candles:

        dt = datetime.fromisoformat(
            candle["time"]
        )

        minute = (
            dt.minute
            -
            dt.minute
            % minutes
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

            groups[key][
                "high"
            ] = max(
                groups[key]["high"],
                candle["high"]
            )

            groups[key][
                "low"
            ] = min(
                groups[key]["low"],
                candle["low"]
            )

            groups[key][
                "close"
            ] = candle["close"]

            groups[key][
                "count"
            ] += 1

    result = [
        candle
        for candle
        in groups.values()
        if candle["count"]
        == minutes
    ]

    return sorted(
        result,
        key=lambda candle:
            candle["time"]
    )


# =====================================================
# EMA
# =====================================================

def ema_series(
    values,
    period
):

    if len(values) < period:

        return []

    multiplier = (
        2.0
        /
        (
            period + 1
        )
    )

    ema = (
        sum(
            values[:period]
        )
        /
        period
    )

    result = [
        ema
    ]

    for value in values[
        period:
    ]:

        ema = (
            value
            *
            multiplier
            +
            ema
            *
            (
                1
                -
                multiplier
            )
        )

        result.append(
            ema
        )

    return result


def last_ema(
    candles,
    period
):

    closes = [
        candle["close"]
        for candle in candles
    ]

    values = ema_series(
        closes,
        period
    )

    if not values:

        return None

    return values[-1]


# =====================================================
# M15 ADAPTIVE TREND
# =====================================================

def m15_direction(candles):

    m15 = build_tf_candles(
        candles,
        15
    )

    if (
        len(m15)
        <
        M15_SLOW_EMA + 3
    ):

        return (
            None,
            None,
            None,
            None,
            "M15_NO_DATA"
        )

    closes = [
        candle["close"]
        for candle in m15
    ]

    ema20 = ema_series(
        closes,
        M15_FAST_EMA
    )

    ema50 = ema_series(
        closes,
        M15_SLOW_EMA
    )

    if (
        len(ema20) < 2
        or
        len(ema50) < 2
    ):

        return (
            None,
            None,
            None,
            None,
            "M15_NO_EMA"
        )

    close_now = closes[-1]

    e20 = ema20[-1]
    e20_previous = ema20[-2]

    e50 = ema50[-1]


    # =================================================
    # STRICT BUY
    # =================================================

    if (
        close_now > e20
        and
        e20 > e50
        and
        e20 > e20_previous
    ):

        return (
            "BUY",
            close_now,
            e20,
            e50,
            "M15_STRICT_BUY"
        )


    # =================================================
    # STRICT SELL
    # =================================================

    if (
        close_now < e20
        and
        e20 < e50
        and
        e20 < e20_previous
    ):

        return (
            "SELL",
            close_now,
            e20,
            e50,
            "M15_STRICT_SELL"
        )


    # =================================================
    # TRANSITION BUY
    # =================================================

    if (
        M15_ALLOW_TRANSITION
        and
        close_now > e20
        and
        e20 > e20_previous
    ):

        return (
            "BUY",
            close_now,
            e20,
            e50,
            "M15_TRANSITION_BUY"
        )


    # =================================================
    # TRANSITION SELL
    # =================================================

    if (
        M15_ALLOW_TRANSITION
        and
        close_now < e20
        and
        e20 < e20_previous
    ):

        return (
            "SELL",
            close_now,
            e20,
            e50,
            "M15_TRANSITION_SELL"
        )


    # =================================================
    # NEUTRAL
    # =================================================

    return (
        None,
        close_now,
        e20,
        e50,
        "M15_NEUTRAL"
    )


# =====================================================
# NORMAL M5 CONFIRMATION
# =====================================================

def m5_confirmation(
    candles,
    side
):

    m5 = build_tf_candles(
        candles,
        5
    )

    if (
        len(m5)
        <
        M5_SLOW_EMA + 3
    ):

        return (
            False,
            None,
            None,
            None
        )

    closes = [
        candle["close"]
        for candle in m5
    ]

    ema9 = ema_series(
        closes,
        M5_FAST_EMA
    )

    ema20 = ema_series(
        closes,
        M5_SLOW_EMA
    )

    if (
        len(ema9) < 2
        or
        len(ema20) < 2
    ):

        return (
            False,
            None,
            None,
            None
        )

    current = m5[-1]
    previous = m5[-2]

    e9 = ema9[-1]
    e9_previous = ema9[-2]
    e20 = ema20[-1]

    if side == "BUY":

        allowed = (
            current["close"]
            >
            current["open"]
            and
            current["close"]
            >
            previous["close"]
            and
            current["close"]
            >
            e9
            >
            e20
            and
            e9
            >
            e9_previous
        )

    else:

        allowed = (
            current["close"]
            <
            current["open"]
            and
            current["close"]
            <
            previous["close"]
            and
            current["close"]
            <
            e9
            <
            e20
            and
            e9
            <
            e9_previous
        )

    return (
        allowed,
        current["close"],
        e9,
        e20
    )


# =====================================================
# V14.4 STRONG M5 REVERSAL
# =====================================================

def m5_strong_reversal(
    candles,
    side
):

    m5 = build_tf_candles(
        candles,
        5
    )

    if (
        len(m5)
        <
        M5_SLOW_EMA + 3
    ):

        return (
            False,
            None,
            None,
            None,
            None
        )

    closes = [
        candle["close"]
        for candle in m5
    ]

    ema9 = ema_series(
        closes,
        M5_FAST_EMA
    )

    ema20 = ema_series(
        closes,
        M5_SLOW_EMA
    )

    if (
        len(ema9) < 2
        or
        len(ema20) < 2
    ):

        return (
            False,
            None,
            None,
            None,
            None
        )

    current = m5[-1]
    previous = m5[-2]

    e9 = ema9[-1]
    e9_previous = ema9[-2]
    e20 = ema20[-1]

    current_range = (
        current["high"]
        -
        current["low"]
    )

    if current_range <= 0:

        return (
            False,
            current["close"],
            e9,
            e20,
            0.0
        )

    body_ratio = (
        abs(
            current["close"]
            -
            current["open"]
        )
        /
        current_range
    )

    if (
        body_ratio
        <
        M5_REVERSAL_MIN_BODY_RATIO
    ):

        return (
            False,
            current["close"],
            e9,
            e20,
            body_ratio
        )


    # =================================================
    # STRONG BUY REVERSAL
    # =================================================

    if side == "BUY":

        basic_confirm = (
            current["close"]
            >
            current["open"]
            and
            current["close"]
            >
            previous["close"]
            and
            current["close"]
            >
            e9
            >
            e20
            and
            e9
            >
            e9_previous
        )

        breakout_confirm = (
            current["close"]
            >
            previous["high"]
        )

        if (
            basic_confirm
            and
            (
                breakout_confirm
                or
                not M5_REVERSAL_REQUIRE_BREAK
            )
        ):

            return (
                True,
                current["close"],
                e9,
                e20,
                body_ratio
            )


    # =================================================
    # STRONG SELL REVERSAL
    # =================================================

    if side == "SELL":

        basic_confirm = (
            current["close"]
            <
            current["open"]
            and
            current["close"]
            <
            previous["close"]
            and
            current["close"]
            <
            e9
            <
            e20
            and
            e9
            <
            e9_previous
        )

        breakout_confirm = (
            current["close"]
            <
            previous["low"]
        )

        if (
            basic_confirm
            and
            (
                breakout_confirm
                or
                not M5_REVERSAL_REQUIRE_BREAK
            )
        ):

            return (
                True,
                current["close"],
                e9,
                e20,
                body_ratio
            )

    return (
        False,
        current["close"],
        e9,
        e20,
        body_ratio
    )


# =====================================================
# M1 STRICT SCALP RETEST
# =====================================================

def m1_scalp_trigger(
    candles,
    side,
    atr
):

    if (
        len(candles)
        <
        M1_SLOW_EMA
        +
        M1_RETEST_LOOKBACK
        +
        3
    ):

        return (
            False,
            None,
            None,
            None,
            None
        )

    current = candles[-1]
    previous = candles[-2]

    current_range = (
        current["high"]
        -
        current["low"]
    )

    if (
        current_range <= 0
        or
        current_range
        >
        atr
        *
        MAX_M1_RANGE_ATR
    ):

        return (
            False,
            None,
            None,
            None,
            None
        )

    ema9 = last_ema(
        candles,
        M1_FAST_EMA
    )

    ema20 = last_ema(
        candles,
        M1_SLOW_EMA
    )

    if (
        ema9 is None
        or
        ema20 is None
    ):

        return (
            False,
            None,
            None,
            None,
            None
        )

    body_ratio = (
        abs(
            current["close"]
            -
            current["open"]
        )
        /
        current_range
    )

    if (
        body_ratio
        <
        M1_MIN_BODY_RATIO
    ):

        return (
            False,
            None,
            None,
            ema9,
            ema20
        )

    lower_wick = (
        min(
            current["open"],
            current["close"]
        )
        -
        current["low"]
    ) / current_range

    upper_wick = (
        current["high"]
        -
        max(
            current["open"],
            current["close"]
        )
    ) / current_range

    tolerance = (
        atr
        *
        M1_RETEST_TOLERANCE_ATR
    )

    recent = candles[
        -(
            M1_RETEST_LOOKBACK
            + 1
        ):
        -1
    ]

    if not recent:

        return (
            False,
            None,
            None,
            ema9,
            ema20
        )


    # =================================================
    # BUY
    # =================================================

    if side == "BUY":

        if not (
            ema9 > ema20
            and
            current["close"] > ema9
        ):

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        retest_found = any(
            candle["low"]
            <=
            ema9
            +
            tolerance
            and
            candle["close"]
            >=
            ema20
            -
            tolerance
            for candle in recent
        )

        if not retest_found:

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        momentum_confirm = (
            current["close"]
            >
            current["open"]
            and
            current["close"]
            >
            previous["close"]
            and
            current["high"]
            >
            previous["high"]
        )

        wick_confirm = (
            current["close"]
            >
            current["open"]
            and
            lower_wick
            >=
            M1_MIN_WICK_RATIO
            and
            current["close"]
            >
            previous["close"]
        )

        if not (
            momentum_confirm
            or
            wick_confirm
        ):

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        if (
            current["close"]
            -
            ema9
            >
            atr
            *
            M1_MAX_EMA9_DISTANCE_ATR
        ):

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        return (
            True,
            current["close"],
            "M1 BUY SCALP RETEST",
            ema9,
            ema20
        )


    # =================================================
    # SELL
    # =================================================

    if side == "SELL":

        if not (
            ema9 < ema20
            and
            current["close"] < ema9
        ):

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        retest_found = any(
            candle["high"]
            >=
            ema9
            -
            tolerance
            and
            candle["close"]
            <=
            ema20
            +
            tolerance
            for candle in recent
        )

        if not retest_found:

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        momentum_confirm = (
            current["close"]
            <
            current["open"]
            and
            current["close"]
            <
            previous["close"]
            and
            current["low"]
            <
            previous["low"]
        )

        wick_confirm = (
            current["close"]
            <
            current["open"]
            and
            upper_wick
            >=
            M1_MIN_WICK_RATIO
            and
            current["close"]
            <
            previous["close"]
        )

        if not (
            momentum_confirm
            or
            wick_confirm
        ):

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        if (
            ema9
            -
            current["close"]
            >
            atr
            *
            M1_MAX_EMA9_DISTANCE_ATR
        ):

            return (
                False,
                None,
                None,
                ema9,
                ema20
            )

        return (
            True,
            current["close"],
            "M1 SELL SCALP RETEST",
            ema9,
            ema20
        )

    return (
        False,
        None,
        None,
        ema9,
        ema20
    )


# =====================================================
# PROTECTIVE SWING
# =====================================================

def recent_protective_swing(
    candles,
    side
):

    n = len(
        candles
    )

    search_start = max(
        SWING_LEFT,
        n
        -
        SL_SWING_LOOKBACK
    )

    search_end = (
        n
        -
        SWING_RIGHT
    )

    candidates = []

    for i in range(
        search_start,
        search_end
    ):

        candle = candles[i]

        left = candles[
            i - SWING_LEFT:
            i
        ]

        right = candles[
            i + 1:
            i + 1 + SWING_RIGHT
        ]

        if (
            len(left)
            <
            SWING_LEFT
            or
            len(right)
            <
            SWING_RIGHT
        ):

            continue

        if side == "BUY":

            if all(
                candle["low"]
                <=
                other["low"]
                for other
                in left + right
            ):

                candidates.append(
                    candle["low"]
                )

        else:

            if all(
                candle["high"]
                >=
                other["high"]
                for other
                in left + right
            ):

                candidates.append(
                    candle["high"]
                )

    if candidates:

        return (
            candidates[-1],
            "CONFIRMED_M1_SWING"
        )

    fallback = candles[
        -(
            SL_FALLBACK_BARS
            + 1
        ):
        -1
    ]

    if not fallback:

        return (
            None,
            None
        )

    if side == "BUY":

        return (
            min(
                candle["low"]
                for candle in fallback
            ),
            "FALLBACK_M1_LOW"
        )

    return (
        max(
            candle["high"]
            for candle in fallback
        ),
        "FALLBACK_M1_HIGH"
    )


# =====================================================
# FRESH SETUP LOCK
# =====================================================

def arm_setup_lock(
    state,
    side,
    reference
):

    state[
        "locked_side"
    ] = side

    state[
        "locked_reference"
    ] = reference

    state[
        "setup_released"
    ] = False

    save_state(
        state
    )

    print(
        "FRESH SETUP LOCK ARMED:",
        side,
        reference,
        flush=True
    )


def update_setup_lock(
    state,
    candles,
    atr
):

    side = state.get(
        "locked_side"
    )

    reference = state.get(
        "locked_reference"
    )

    if (
        side is None
        or
        reference is None
        or
        state.get(
            "setup_released",
            True
        )
    ):

        return

    if not candles or atr <= 0:

        return

    last_close = candles[-1][
        "close"
    ]

    ema20 = last_ema(
        candles,
        M1_SLOW_EMA
    )

    if ema20 is None:

        return

    release_distance = (
        atr
        *
        NEW_SETUP_RELEASE_ATR
    )

    reset_by_ema20 = False
    reset_by_distance = False

    if side == "BUY":

        reset_by_ema20 = (
            last_close
            <=
            ema20
        )

        reset_by_distance = (
            last_close
            <=
            reference
            -
            release_distance
        )

    elif side == "SELL":

        reset_by_ema20 = (
            last_close
            >=
            ema20
        )

        reset_by_distance = (
            last_close
            >=
            reference
            +
            release_distance
        )

    if not (
        reset_by_ema20
        or
        reset_by_distance
    ):

        return

    old_side = side

    reason = (
        "EMA20 RESET"
        if reset_by_ema20
        else
        "ATR PULLBACK RESET"
    )

    state[
        "setup_released"
    ] = True

    state[
        "locked_side"
    ] = None

    state[
        "locked_reference"
    ] = None

    state[
        "last_signal"
    ] = None

    save_state(
        state
    )

    notify(
        "RIO GOLD V14.4 OLD SETUP RESET\n"
        f"OLD SIDE: {old_side}\n"
        f"RESET: {reason}\n"
        "NEW FRESH SETUP CAN FORM"
    )


def same_setup_blocked(
    state,
    side
):

    return (
        state.get(
            "locked_side"
        )
        == side
        and
        not state.get(
            "setup_released",
            True
        )
    )


# =====================================================
# MARKET
# =====================================================

async def get_market(connection):

    spec = await meta_call(
        lambda:
        connection
        .get_symbol_specification(
            SYMBOL
        )
    )

    price = await meta_call(
        lambda:
        connection
        .get_symbol_price(
            SYMBOL
        )
    )

    if (
        not spec
        or
        not price
    ):

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
        spec.get(
            "tickSize"
        )
        or
        10 ** (
            -digits
        )
    )

    bid = float(
        price["bid"]
    )

    ask = float(
        price["ask"]
    )

    if (
        tick <= 0
        or
        bid <= 0
        or
        ask <= bid
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

    tick = market[
        "tick"
    ]

    return round(
        round(
            value
            /
            tick
        )
        *
        tick,
        market[
            "digits"
        ]
    )


# =====================================================
# SPREAD
# =====================================================

def spread_is_safe(
    market,
    atr
):

    spread = (
        market["ask"]
        -
        market["bid"]
    )

    if (
        spread
        >
        MAX_SPREAD
    ):

        return (
            False,
            spread,
            "ABSOLUTE SPREAD TOO HIGH"
        )

    if (
        atr > 0
        and
        spread
        >
        atr
        *
        MAX_SPREAD_ATR
    ):

        return (
            False,
            spread,
            "SPREAD TOO LARGE VS ATR"
        )

    return (
        True,
        spread,
        None
    )


# =====================================================
# NO CHASE
# =====================================================

def validate_live_entry(
    side,
    entry,
    signal_close,
    atr
):

    max_forward = (
        atr
        *
        MAX_FORWARD_DRIFT_ATR
    )

    max_adverse = (
        atr
        *
        MAX_ADVERSE_DRIFT_ATR
    )

    if side == "BUY":

        forward_move = (
            entry
            -
            signal_close
        )

        adverse_move = (
            signal_close
            -
            entry
        )

    else:

        forward_move = (
            signal_close
            -
            entry
        )

        adverse_move = (
            entry
            -
            signal_close
        )

    if (
        forward_move
        >
        max_forward
    ):

        return (
            False,
            (
                "CHASE TOO FAR "
                f"{forward_move:.2f} "
                f"> {max_forward:.2f}"
            )
        )

    if (
        adverse_move
        >
        max_adverse
    ):

        return (
            False,
            (
                "SIGNAL LOST "
                f"{adverse_move:.2f} "
                f"> {max_adverse:.2f}"
            )
        )

    return (
        True,
        "OK"
    )


# =====================================================
# BROKER MIN STOP
# =====================================================

def broker_min_stop_distance(
    market
):

    spec = market[
        "spec"
    ]

    tick = market[
        "tick"
    ]

    point = float(
        spec.get(
            "point"
        )
        or
        10 ** (
            -market[
                "digits"
            ]
        )
    )

    stops_level = float(
        spec.get(
            "stopsLevel"
        )
        or 0
    )

    freeze_level = float(
        spec.get(
            "freezeLevel"
        )
        or 0
    )

    broker_distance = (
        max(
            stops_level,
            freeze_level
        )
        *
        point
    )

    return max(
        broker_distance
        +
        tick,
        tick * 2
    )


# =====================================================
# TP GROUP
# =====================================================

def tp_group_for_order(
    number
):

    if (
        number
        <=
        TP1_COUNT
    ):

        return (
            "TP1",
            TP1_RR
        )

    if (
        number
        <=
        TP1_COUNT
        +
        TP2_COUNT
    ):

        return (
            "TP2",
            TP2_RR
        )

    if (
        number
        <=
        TP1_COUNT
        +
        TP2_COUNT
        +
        TP3_COUNT
    ):

        return (
            "TP3",
            TP3_RR
        )

    return (
        "TP4",
        TP4_RR
    )


# =====================================================
# COMMON SL + TP
# =====================================================

def get_common_levels(
    side,
    reference_entry,
    anchor,
    atr,
    market
):

    structural_buffer = (
        atr
        *
        SL_SWING_BUFFER_ATR
    )

    min_broker_distance = (
        broker_min_stop_distance(
            market
        )
    )

    min_risk = max(
        MIN_SL_DISTANCE,
        atr
        *
        MIN_SL_ATR_MULT,
        min_broker_distance
        +
        market["tick"]
    )

    max_risk = min(
        MAX_SL_DISTANCE,
        atr
        *
        MAX_SL_ATR_MULT
    )

    if side == "BUY":

        desired_sl = (
            anchor
            -
            structural_buffer
        )

        structural_risk = (
            reference_entry
            -
            desired_sl
        )

    else:

        desired_sl = (
            anchor
            +
            structural_buffer
        )

        structural_risk = (
            desired_sl
            -
            reference_entry
        )

    risk = max(
        structural_risk,
        min_risk
    )

    if risk <= 0:

        return (
            None,
            "INVALID RISK"
        )

    if (
        risk
        >
        max_risk
    ):

        return (
            None,
            (
                "SL TOO LARGE "
                f"RISK={risk:.2f} "
                f"MAX={max_risk:.2f}"
            )
        )

    if side == "BUY":

        sl = normalize(
            reference_entry
            -
            risk,
            market
        )

        tps = {
            "TP1":
                normalize(
                    reference_entry
                    +
                    risk
                    *
                    TP1_RR,
                    market
                ),

            "TP2":
                normalize(
                    reference_entry
                    +
                    risk
                    *
                    TP2_RR,
                    market
                ),

            "TP3":
                normalize(
                    reference_entry
                    +
                    risk
                    *
                    TP3_RR,
                    market
                ),

            "TP4":
                normalize(
                    reference_entry
                    +
                    risk
                    *
                    TP4_RR,
                    market
                )
        }

    else:

        sl = normalize(
            reference_entry
            +
            risk,
            market
        )

        tps = {
            "TP1":
                normalize(
                    reference_entry
                    -
                    risk
                    *
                    TP1_RR,
                    market
                ),

            "TP2":
                normalize(
                    reference_entry
                    -
                    risk
                    *
                    TP2_RR,
                    market
                ),

            "TP3":
                normalize(
                    reference_entry
                    -
                    risk
                    *
                    TP3_RR,
                    market
                ),

            "TP4":
                normalize(
                    reference_entry
                    -
                    risk
                    *
                    TP4_RR,
                    market
                )
        }

    return {
        "reference_entry":
            reference_entry,

        "sl":
            sl,

        "tps":
            tps,

        "risk":
            abs(
                reference_entry
                -
                sl
            )
    }, None


def common_levels_valid_for_market(
    side,
    sl,
    tp,
    market
):

    min_distance = (
        broker_min_stop_distance(
            market
        )
    )

    if side == "BUY":

        if (
            sl
            >=
            market["bid"]
            -
            min_distance
        ):

            return (
                False,
                "COMMON BUY SL TOO CLOSE"
            )

        if (
            tp
            <=
            market["ask"]
            +
            min_distance
        ):

            return (
                False,
                "COMMON BUY TP TOO CLOSE"
            )

    else:

        if (
            sl
            <=
            market["ask"]
            +
            min_distance
        ):

            return (
                False,
                "COMMON SELL SL TOO CLOSE"
            )

        if (
            tp
            >=
            market["bid"]
            -
            min_distance
        ):

            return (
                False,
                "COMMON SELL TP TOO CLOSE"
            )

    return (
        True,
        "OK"
    )


# =====================================================
# RR FROM COMMENT
# =====================================================

def rr_from_comment(comment):

    value = str(
        comment
        or ""
    ).upper()

    if "TP1" in value:

        return (
            "TP1",
            TP1_RR
        )

    if "TP2" in value:

        return (
            "TP2",
            TP2_RR
        )

    if "TP3" in value:

        return (
            "TP3",
            TP3_RR
        )

    if "TP4" in value:

        return (
            "TP4",
            TP4_RR
        )

    return (
        None,
        None
    )


# =====================================================
# WAIT FOR COMPLETE BATCH
# =====================================================

async def wait_for_batch_positions(
    connection,
    previous_ids,
    side
):

    best_matches = []

    for attempt in range(
        1,
        BATCH_VERIFY_ATTEMPTS + 1
    ):

        try:

            positions = await get_positions(
                connection
            )

        except MetaApiTemporaryError:

            await asyncio.sleep(
                BATCH_VERIFY_DELAY
            )

            continue

        matches = [
            position
            for position in positions
            if (
                str(
                    position.get(
                        "id",
                        ""
                    )
                )
                not in previous_ids
                and
                position_side(
                    position
                )
                == side
                and
                is_managed_position(
                    position
                )
            )
        ]

        if (
            len(matches)
            >
            len(best_matches)
        ):

            best_matches = matches

        print(
            "GOLD V14.4 BATCH VERIFY:",
            f"{len(matches)}/{BATCH_SIZE}",
            f"ATTEMPT={attempt}",
            flush=True
        )

        if (
            len(matches)
            >=
            BATCH_SIZE
        ):

            return matches[
                :BATCH_SIZE
            ]

        await asyncio.sleep(
            BATCH_VERIFY_DELAY
        )

    return best_matches


# =====================================================
# REGISTER BATCH POSITIONS
# =====================================================

def register_batch_positions(
    state,
    positions
):

    registered = 0

    for position in positions:

        pid = str(
            position["id"]
        )

        side = position_side(
            position
        )

        if side is None:

            continue

        entry = float(
            position.get(
                "openPrice"
            )
            or 0
        )

        sl = float(
            position.get(
                "stopLoss"
            )
            or 0
        )

        tp = float(
            position.get(
                "takeProfit"
            )
            or 0
        )

        if (
            entry <= 0
            or
            sl <= 0
        ):

            continue

        group, rr = rr_from_comment(
            position.get(
                "comment"
            )
        )

        risk = abs(
            entry
            -
            sl
        )

        if risk <= 0:

            continue

        state[
            "positions"
        ][pid] = {
            "risk":
                risk,

            "entry":
                entry,

            "side":
                side,

            "tp_group":
                group
                or "UNKNOWN",

            "tp_rr":
                rr
                or 0
        }

        registered += 1

    state[
        "last_trade_time"
    ] = time.time()

    save_state(
        state
    )

    return registered


# =====================================================
# OPEN FAST 12-POSITION BATCH
# =====================================================

async def open_batch(
    connection,
    side,
    signal_close,
    trigger_mode,
    m15_mode,
    m1_ema9,
    m1_ema20,
    sl_anchor,
    sl_anchor_mode,
    atr,
    state
):

    if (
        state["halted"]
        or
        state["order_uncertain"]
    ):

        return

    if same_setup_blocked(
        state,
        side
    ):

        print(
            "GOLD V14.4 BLOCKED: "
            "OLD SAME-DIRECTION SETUP",
            side,
            flush=True
        )

        return

    existing_positions = (
        await get_positions(
            connection
        )
    )

    if existing_positions:

        print(
            "GOLD V14.4 WAIT: "
            "XAUUSD POSITION ALREADY OPEN",
            len(
                existing_positions
            ),
            flush=True
        )

        return


    # =================================================
    # MARKET
    # =================================================

    market = await get_market(
        connection
    )

    (
        spread_ok,
        spread,
        spread_reason
    ) = spread_is_safe(
        market,
        atr
    )

    if not spread_ok:

        print(
            "GOLD V14.4 ENTRY BLOCKED:",
            spread_reason,
            f"SPREAD={spread:.2f}",
            flush=True
        )

        return

    reference_entry = (
        market["ask"]
        if side == "BUY"
        else market["bid"]
    )


    # =================================================
    # NO CHASE - IBA PRED BATCHOM
    # =================================================

    (
        live_ok,
        live_reason
    ) = validate_live_entry(
        side,
        reference_entry,
        signal_close,
        atr
    )

    if not live_ok:

        print(
            "GOLD V14.4 ENTRY BLOCKED:",
            live_reason,
            flush=True
        )

        return


    # =================================================
    # EMA9 DISTANCE
    # =================================================

    if side == "BUY":

        if (
            reference_entry
            -
            m1_ema9
            >
            atr
            *
            M1_MAX_EMA9_DISTANCE_ATR
        ):

            print(
                "GOLD V14.4 BLOCKED: "
                "TOO FAR ABOVE M1 EMA9",
                flush=True
            )

            return

    else:

        if (
            m1_ema9
            -
            reference_entry
            >
            atr
            *
            M1_MAX_EMA9_DISTANCE_ATR
        ):

            print(
                "GOLD V14.4 BLOCKED: "
                "TOO FAR BELOW M1 EMA9",
                flush=True
            )

            return


    # =================================================
    # SL ANCHOR
    # =================================================

    if (
        side == "BUY"
        and
        sl_anchor
        >=
        reference_entry
    ):

        print(
            "GOLD V14.4 BLOCKED: "
            "BUY SL ANCHOR INVALID",
            flush=True
        )

        return

    if (
        side == "SELL"
        and
        sl_anchor
        <=
        reference_entry
    ):

        print(
            "GOLD V14.4 BLOCKED: "
            "SELL SL ANCHOR INVALID",
            flush=True
        )

        return


    # =================================================
    # COMMON LEVELS
    # =================================================

    levels, reason = get_common_levels(
        side,
        reference_entry,
        sl_anchor,
        atr,
        market
    )

    if levels is None:

        print(
            "GOLD V14.4 ENTRY BLOCKED BY SL:",
            reason,
            flush=True
        )

        return


    # =================================================
    # VALIDATE ALL TP LEVELS
    # =================================================

    for group in (
        "TP1",
        "TP2",
        "TP3",
        "TP4"
    ):

        valid, valid_reason = (
            common_levels_valid_for_market(
                side,
                levels["sl"],
                levels["tps"][group],
                market
            )
        )

        if not valid:

            print(
                "GOLD V14.4 ENTRY BLOCKED:",
                group,
                valid_reason,
                flush=True
            )

            return


    # =================================================
    # LOT CHECK
    # =================================================

    spec = market[
        "spec"
    ]

    min_volume = float(
        spec.get(
            "minVolume"
        )
        or 0.01
    )

    volume_step = float(
        spec.get(
            "volumeStep"
        )
        or 0.01
    )

    if (
        LOT_SIZE
        <
        min_volume
        or
        volume_step
        <= 0
        or
        abs(
            LOT_SIZE
            /
            volume_step
            -
            round(
                LOT_SIZE
                /
                volume_step
            )
        )
        >
        1e-7
    ):

        notify(
            "RIO GOLD V14.4 INVALID LOT"
        )

        return


    # =================================================
    # TEST MODE
    # =================================================

    if not ENABLE_TRADING:

        notify(
            "RIO GOLD V14.4 TEST SIGNAL\n"
            f"SIDE: {side}\n"
            f"M15 MODE: {m15_mode}\n"
            f"M1: {trigger_mode}\n"
            f"EMA9: {m1_ema9:.2f}\n"
            f"EMA20: {m1_ema20:.2f}\n"
            f"REF ENTRY: {reference_entry:.2f}\n"
            f"COMMON SL: {levels['sl']:.2f}\n"
            f"TP1: {levels['tps']['TP1']:.2f}\n"
            f"TP2: {levels['tps']['TP2']:.2f}\n"
            f"TP3: {levels['tps']['TP3']:.2f}\n"
            f"TP4: {levels['tps']['TP4']:.2f}\n"
            f"RISK: {levels['risk']:.2f}\n"
            f"ATR: {atr:.2f}"
        )

        return


    # =================================================
    # ARM SETUP LOCK
    # =================================================

    arm_setup_lock(
        state,
        side,
        signal_close
    )

    state[
        "order_uncertain"
    ] = True

    save_state(
        state
    )


    # =================================================
    # POSITIONS BEFORE BATCH
    # =================================================

    before_positions = await get_positions(
        connection
    )

    previous_ids = {
        str(
            position["id"]
        )
        for position in before_positions
    }

    if previous_ids:

        state[
            "order_uncertain"
        ] = False

        save_state(
            state
        )

        print(
            "GOLD V14.4 BATCH CANCELLED: "
            "POSITION APPEARED BEFORE BATCH",
            flush=True
        )

        return


    # =================================================
    # ENTRY APPROVED
    # =================================================

    notify(
        "RIO GOLD V14.4 ENTRY APPROVED\n"
        f"SIDE: {side}\n"
        f"M15 MODE: {m15_mode}\n"
        "M5 CONFIRMATION: OK\n"
        f"M1 SCALP: {trigger_mode}\n"
        f"EMA9: {m1_ema9:.2f}\n"
        f"EMA20: {m1_ema20:.2f}\n"
        f"REF ENTRY: {reference_entry:.2f}\n"
        f"SL ANCHOR: {sl_anchor:.2f}\n"
        f"SL MODE: {sl_anchor_mode}\n"
        f"COMMON SL: {levels['sl']:.2f}\n"
        f"TP1: {levels['tps']['TP1']:.2f}\n"
        f"TP2: {levels['tps']['TP2']:.2f}\n"
        f"TP3: {levels['tps']['TP3']:.2f}\n"
        f"TP4: {levels['tps']['TP4']:.2f}\n"
        f"RISK: {levels['risk']:.2f}\n"
        f"ATR: {atr:.2f}\n"
        f"SPREAD: {spread:.2f}\n"
        "FAST BATCH LOCKED\n"
        "OPENING 12 POSITIONS"
    )


    # =================================================
    # FAST ORDER SEND
    # =================================================

    sent_count = 0

    try:

        for number in range(
            1,
            BATCH_SIZE + 1
        ):

            (
                tp_group,
                tp_rr
            ) = tp_group_for_order(
                number
            )

            common_sl = levels[
                "sl"
            ]

            common_tp = levels[
                "tps"
            ][
                tp_group
            ]

            comment = (
                f"{COMMENT_PREFIX}_"
                f"{tp_group}_"
                f"{number:02d}"
            )

            options = {
                "comment": comment
            }

            if side == "BUY":

                result = await meta_call(
                    lambda
                    sl=common_sl,
                    tp=common_tp,
                    opts=options:
                    connection
                    .create_market_buy_order(
                        SYMBOL,
                        LOT_SIZE,
                        sl,
                        tp,
                        opts
                    ),
                    timeout=ORDER_TIMEOUT
                )

            else:

                result = await meta_call(
                    lambda
                    sl=common_sl,
                    tp=common_tp,
                    opts=options:
                    connection
                    .create_market_sell_order(
                        SYMBOL,
                        LOT_SIZE,
                        sl,
                        tp,
                        opts
                    ),
                    timeout=ORDER_TIMEOUT
                )

            sent_count += 1

            print(
                "GOLD V14.4 ORDER SENT",
                f"{number}/{BATCH_SIZE}",
                tp_group,
                result,
                flush=True
            )


        # =================================================
        # VERIFY AFTER ALL ORDERS
        # =================================================

        new_positions = (
            await wait_for_batch_positions(
                connection,
                previous_ids,
                side
            )
        )

        registered = register_batch_positions(
            state,
            new_positions
        )

        if (
            len(new_positions)
            <
            BATCH_SIZE
        ):

            state[
                "order_uncertain"
            ] = True

            state[
                "halted"
            ] = True

            save_state(
                state
            )

            notify(
                "RIO GOLD V14.4 PARTIAL BATCH\n"
                f"SENT: {sent_count}/12\n"
                f"VISIBLE: {len(new_positions)}/12\n"
                f"TRACKED: {registered}\n"
                "NO NEW BATCH UNTIL RECONCILED"
            )

            return


        # =================================================
        # SUCCESS
        # =================================================

        state[
            "order_uncertain"
        ] = False

        state[
            "halted"
        ] = False

        state[
            "last_trade_time"
        ] = time.time()

        save_state(
            state
        )

        notify(
            "RIO GOLD V14.4 BATCH COMPLETED\n"
            "12/12 OPENED\n"
            f"LOT EACH: {LOT_SIZE}\n"
            f"TOTAL: "
            f"{LOT_SIZE * BATCH_SIZE:.2f} LOT\n"
            "TP1: 3 x 0.40R\n"
            "TP2: 3 x 0.70R\n"
            "TP3: 3 x 1.00R\n"
            "TP4: 3 x 1.30R\n"
            "BE1: 0.30R -> +0.05R\n"
            "BE2: 0.60R -> +0.25R\n"
            "BE3: 0.90R -> +0.50R"
        )


    # =================================================
    # BATCH ERROR
    # =================================================

    except Exception as e:

        print(
            "GOLD V14.4 BATCH ERROR:",
            traceback.format_exc(),
            flush=True
        )

        try:

            partial_positions = (
                await wait_for_batch_positions(
                    connection,
                    previous_ids,
                    side
                )
            )

            if partial_positions:

                register_batch_positions(
                    state,
                    partial_positions
                )

        except Exception:

            pass

        state[
            "order_uncertain"
        ] = True

        state[
            "halted"
        ] = True

        save_state(
            state
        )

        notify(
            "RIO GOLD V14.4 BATCH STOPPED\n"
            f"SENT BEFORE ERROR: "
            f"{sent_count}/12\n"
            f"ERROR: {type(e).__name__}\n"
            f"{str(e)[:180]}"
        )


# =====================================================
# 3-STAGE BREAK EVEN
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
    ].get(
        pid
    )

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
        position.get(
            "stopLoss"
        )
        or 0
    )

    current_tp = position.get(
        "takeProfit"
    )

    if (
        risk <= 0
        or
        current_sl <= 0
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
        price
        -
        entry
    ) * direction


    # =================================================
    # BE3
    # =================================================

    if (
        profit_distance
        >=
        risk
        *
        BE3_TRIGGER_RR
    ):

        trigger_rr = (
            BE3_TRIGGER_RR
        )

        lock_rr = (
            BE3_LOCK_RR
        )

        stage = "BE3"


    # =================================================
    # BE2
    # =================================================

    elif (
        profit_distance
        >=
        risk
        *
        BE2_TRIGGER_RR
    ):

        trigger_rr = (
            BE2_TRIGGER_RR
        )

        lock_rr = (
            BE2_LOCK_RR
        )

        stage = "BE2"


    # =================================================
    # BE1
    # =================================================

    elif (
        profit_distance
        >=
        risk
        *
        BE1_TRIGGER_RR
    ):

        trigger_rr = (
            BE1_TRIGGER_RR
        )

        lock_rr = (
            BE1_LOCK_RR
        )

        stage = "BE1"

    else:

        return


    wanted_sl = (
        entry
        +
        direction
        *
        risk
        *
        lock_rr
    )

    wanted_sl = normalize(
        wanted_sl,
        market
    )

    tick = market[
        "tick"
    ]


    # =================================================
    # ONLY IMPROVE SL
    # =================================================

    if side == "BUY":

        if (
            wanted_sl
            <=
            current_sl
            +
            tick / 2
        ):

            return

    else:

        if (
            wanted_sl
            >=
            current_sl
            -
            tick / 2
        ):

            return


    # =================================================
    # BROKER DISTANCE
    # =================================================

    min_distance = (
        broker_min_stop_distance(
            market
        )
    )

    if side == "BUY":

        if (
            wanted_sl
            >=
            market["bid"]
            -
            min_distance
        ):

            return

    else:

        if (
            wanted_sl
            <=
            market["ask"]
            +
            min_distance
        ):

            return


    # =================================================
    # MODIFY
    # =================================================

    await meta_call(
        lambda:
        connection.modify_position(
            position["id"],
            wanted_sl,
            current_tp
        ),
        timeout=25
    )

    notify(
        f"RIO GOLD V14.4 {stage} ACTIVE\n"
        f"POSITION: {pid}\n"
        f"SIDE: {side}\n"
        f"GROUP: "
        f"{info.get('tp_group')}\n"
        f"ENTRY: {entry}\n"
        f"RISK: {risk:.2f}\n"
        f"TRIGGER: {trigger_rr:.2f}R\n"
        f"SL LOCK: {wanted_sl}\n"
        f"LOCK: +{lock_rr:.2f}R"
    )


# =====================================================
# ADOPT POSITIONS
# =====================================================

async def adopt_positions(
    connection,
    state
):

    positions = await get_positions(
        connection
    )

    managed = [
        position
        for position in positions
        if is_managed_position(
            position
        )
    ]

    adopted = 0

    for position in managed:

        pid = str(
            position["id"]
        )

        if (
            pid
            in state[
                "positions"
            ]
        ):

            continue

        side = position_side(
            position
        )

        if side is None:

            continue

        entry = float(
            position.get(
                "openPrice"
            )
            or 0
        )

        sl = float(
            position.get(
                "stopLoss"
            )
            or 0
        )

        tp = float(
            position.get(
                "takeProfit"
            )
            or 0
        )

        if entry <= 0:

            continue

        group, rr = rr_from_comment(
            position.get(
                "comment"
            )
        )

        if (
            tp > 0
            and
            rr is not None
            and
            rr > 0
        ):

            risk = (
                abs(
                    tp
                    -
                    entry
                )
                /
                rr
            )

        elif sl > 0:

            risk = abs(
                entry
                -
                sl
            )

        else:

            continue

        if risk <= 0:

            continue

        state[
            "positions"
        ][pid] = {
            "risk":
                risk,

            "entry":
                entry,

            "side":
                side,

            "tp_group":
                group
                or "UNKNOWN",

            "tp_rr":
                rr
                or 0
        }

        adopted += 1


    if managed:

        state[
            "order_uncertain"
        ] = False

        state[
            "halted"
        ] = False

    save_state(
        state
    )

    if adopted:

        notify(
            "RIO GOLD V14.4 POSITIONS ADOPTED\n"
            f"BOT OPEN: {len(managed)}/12\n"
            f"TRACKED: "
            f"{len(state['positions'])}/12\n"
            "BE PROTECTION RESTORED"
        )

    return managed


# =====================================================
# RECONCILE
# =====================================================

async def reconcile_state(
    connection,
    state
):

    positions = await get_positions(
        connection
    )

    managed = [
        position
        for position in positions
        if is_managed_position(
            position
        )
    ]

    current_ids = {
        str(
            position["id"]
        )
        for position in managed
    }

    known_ids = set(
        state[
            "positions"
        ].keys()
    )

    closed_ids = (
        known_ids
        -
        current_ids
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

        notify(
            "RIO GOLD V14.4 POSITION CLOSED\n"
            f"ID: {pid}\n"
            f"REMAINING BOT: "
            f"{len(state['positions'])}"
        )

    if managed:

        await adopt_positions(
            connection,
            state
        )

    elif not state[
        "positions"
    ]:

        state[
            "order_uncertain"
        ] = False

        state[
            "halted"
        ] = False

    save_state(
        state
    )

    return (
        positions,
        managed
    )


# =====================================================
# SESSION
# =====================================================

async def bot_session(state):

    api = MetaApi(
        M_TOKEN
    )

    account = await meta_call(
        lambda:
        api.metatrader_account_api
        .get_account(
            M_ACC
        ),
        timeout=40,
        retries=4
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
        str(
            account.state
        ).upper()
        != "DEPLOYED"
    ):

        await meta_call(
            lambda:
            account.deploy(),
            timeout=60,
            retries=4
        )

    await meta_call(
        lambda:
        account.wait_connected(),
        timeout=CONNECT_TIMEOUT,
        retries=4
    )

    connection = (
        account.get_rpc_connection()
    )

    try:

        await meta_call(
            lambda:
            connection.connect(),
            timeout=CONNECT_TIMEOUT,
            retries=4
        )

        await meta_call(
            lambda:
            connection.wait_synchronized(),
            timeout=CONNECT_TIMEOUT,
            retries=4
        )

        (
            all_positions,
            managed
        ) = await reconcile_state(
            connection,
            state
        )

        try:

            await get_current_m1(
                region,
                force=True
            )

        except Exception as e:

            print(
                "INITIAL CURRENT M1 WARMUP ERROR:",
                e,
                flush=True
            )


        # =================================================
        # CONNECTED
        # =================================================

        notify(
            "RIO GOLD V14.4 CONNECTED\n"
            "STRONG M5 REVERSAL ACTIVE\n"
            "ADAPTIVE M15 ACTIVE\n"
            "STRICT SCALP ACTIVE\n"
            "FAST BATCH ACTIVE\n"
            "NO CHASE PRE-BATCH ONLY\n"
            "M15 STRICT + TRANSITION ACTIVE\n"
            "M15 NEUTRAL -> M5 FALLBACK ACTIVE\n"
            "M15 OLD TREND -> STRONG M5 OVERRIDE ACTIVE\n"
            "M5 EMA9/20 CONFIRM ACTIVE\n"
            "M5 BODY + HIGH/LOW BREAK ACTIVE\n"
            "M1 EMA9/20 RETEST ACTIVE\n"
            "M1 MOMENTUM CONFIRM ACTIVE\n"
            "COMMON SL ACTIVE\n"
            "COMMON TP LEVELS ACTIVE\n"
            "FRESH M1 FIX ACTIVE\n"
            "NEWS FILTER: OFF\n"
            "MANUAL XAUUSD PROTECTION ACTIVE\n"
            "FRESH SETUP RESET ACTIVE\n"
            "SAME-DIRECTION REENTRY PROTECTION ACTIVE\n"
            f"POST-BATCH COOLDOWN: {COOLDOWN_SECONDS}s\n"
            "CONTINUOUS MODE ACTIVE\n"
            "12 POSITIONS / ONE SIGNAL\n"
            f"LOT EACH: {LOT_SIZE}\n"
            f"TOTAL BATCH: "
            f"{LOT_SIZE * BATCH_SIZE:.2f} LOT\n"
            "TP1: 3 x 0.40R\n"
            "TP2: 3 x 0.70R\n"
            "TP3: 3 x 1.00R\n"
            "TP4: 3 x 1.30R\n"
            "BE1: 0.30R -> +0.05R\n"
            "BE2: 0.60R -> +0.25R\n"
            "BE3: 0.90R -> +0.50R\n"
            f"SL MIN: {MIN_SL_DISTANCE:.2f}\n"
            f"SL MAX: {MAX_SL_DISTANCE:.2f}\n"
            f"MAX SPREAD: {MAX_SPREAD:.2f}\n"
            f"BOT OPEN: {len(managed)}/12\n"
            f"ALL XAUUSD OPEN: "
            f"{len(all_positions)}\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        consecutive_rpc_failures = 0


        # =================================================
        # MAIN LOOP
        # =================================================

        while True:

            try:

                (
                    all_positions,
                    managed
                ) = await reconcile_state(
                    connection,
                    state
                )

                consecutive_rpc_failures = 0


                # =====================================
                # OPEN POSITION EXISTS
                # =====================================

                if all_positions:

                    if managed:

                        market = await get_market(
                            connection
                        )

                        for position in managed:

                            await protect_position(
                                connection,
                                position,
                                state,
                                market
                            )

                    manual_count = (
                        len(
                            all_positions
                        )
                        -
                        len(
                            managed
                        )
                    )

                    if manual_count > 0:

                        print(
                            "GOLD V14.4 WAIT: "
                            "MANUAL/OTHER XAUUSD OPEN:",
                            manual_count,
                            flush=True
                        )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # COOLDOWN
                # =====================================

                cooldown_left = (
                    COOLDOWN_SECONDS
                    -
                    (
                        time.time()
                        -
                        state[
                            "last_trade_time"
                        ]
                    )
                )

                if cooldown_left > 0:

                    print(
                        "GOLD V14.4 WAIT: "
                        "POST-BATCH COOLDOWN",
                        f"{cooldown_left:.0f}s LEFT",
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # DATA
                # =====================================

                candles = await get_candles(
                    region
                )

                last_age = float(
                    candles[-1].get(
                        "age_seconds",
                        999999
                    )
                )

                if (
                    last_age
                    >
                    MAX_ENTRY_CANDLE_AGE
                ):

                    print(
                        "GOLD V14.4 WAIT: "
                        "M1 NOT FRESH",
                        f"AGE={last_age:.0f}s",
                        flush=True
                    )

                    await asyncio.sleep(
                        STALE_DATA_RETRY_SECONDS
                    )

                    continue

                candle_time = (
                    candles[-1][
                        "time"
                    ]
                )

                if (
                    candle_time
                    ==
                    state[
                        "last_candle"
                    ]
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                state[
                    "last_candle"
                ] = candle_time

                save_state(
                    state
                )


                # =====================================
                # ATR
                # =====================================

                atr = calculate_atr(
                    candles
                )

                if (
                    atr is None
                    or
                    atr <= 0
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # FRESH SETUP RESET
                # =====================================

                update_setup_lock(
                    state,
                    candles,
                    atr
                )


                # =====================================
                # M15
                # =====================================

                (
                    direction,
                    m15_close,
                    m15_ema20,
                    m15_ema50,
                    m15_mode
                ) = m15_direction(
                    candles
                )

                original_m15_direction = direction
                original_m15_mode = m15_mode


                # =====================================
                # CHECK BOTH NORMAL M5 DIRECTIONS
                # =====================================

                (
                    m5_buy_ok,
                    m5_buy_close,
                    m5_buy_ema9,
                    m5_buy_ema20
                ) = m5_confirmation(
                    candles,
                    "BUY"
                )

                (
                    m5_sell_ok,
                    m5_sell_close,
                    m5_sell_ema9,
                    m5_sell_ema20
                ) = m5_confirmation(
                    candles,
                    "SELL"
                )


                # =====================================
                # CHECK BOTH STRONG M5 REVERSALS
                # =====================================

                (
                    strong_buy,
                    strong_buy_close,
                    strong_buy_ema9,
                    strong_buy_ema20,
                    strong_buy_body
                ) = m5_strong_reversal(
                    candles,
                    "BUY"
                )

                (
                    strong_sell,
                    strong_sell_close,
                    strong_sell_ema9,
                    strong_sell_ema20,
                    strong_sell_body
                ) = m5_strong_reversal(
                    candles,
                    "SELL"
                )


                # =====================================
                # M15 NEUTRAL
                # M5 DECIDES
                # =====================================

                if direction is None:

                    if not M15_ALLOW_M5_FALLBACK:

                        print(
                            "GOLD V14.4 WAIT: "
                            "M15 NEUTRAL",
                            flush=True
                        )

                        await asyncio.sleep(
                            LOOP_SECONDS
                        )

                        continue

                    buy_candidate = (
                        m5_buy_ok
                        or
                        strong_buy
                    )

                    sell_candidate = (
                        m5_sell_ok
                        or
                        strong_sell
                    )

                    if (
                        buy_candidate
                        and
                        not sell_candidate
                    ):

                        direction = "BUY"

                        if m5_buy_ok:

                            m5_close = (
                                m5_buy_close
                            )

                            m5_ema9 = (
                                m5_buy_ema9
                            )

                            m5_ema20 = (
                                m5_buy_ema20
                            )

                            m15_mode = (
                                "M15_NEUTRAL_M5_BUY"
                            )

                        else:

                            m5_close = (
                                strong_buy_close
                            )

                            m5_ema9 = (
                                strong_buy_ema9
                            )

                            m5_ema20 = (
                                strong_buy_ema20
                            )

                            m15_mode = (
                                "M15_NEUTRAL_STRONG_M5_BUY"
                            )

                        m5_ok = True

                        print(
                            "GOLD V14.4 M15 NEUTRAL -> "
                            "M5 BUY",
                            f"STRONG={strong_buy}",
                            flush=True
                        )


                    elif (
                        sell_candidate
                        and
                        not buy_candidate
                    ):

                        direction = "SELL"

                        if m5_sell_ok:

                            m5_close = (
                                m5_sell_close
                            )

                            m5_ema9 = (
                                m5_sell_ema9
                            )

                            m5_ema20 = (
                                m5_sell_ema20
                            )

                            m15_mode = (
                                "M15_NEUTRAL_M5_SELL"
                            )

                        else:

                            m5_close = (
                                strong_sell_close
                            )

                            m5_ema9 = (
                                strong_sell_ema9
                            )

                            m5_ema20 = (
                                strong_sell_ema20
                            )

                            m15_mode = (
                                "M15_NEUTRAL_STRONG_M5_SELL"
                            )

                        m5_ok = True

                        print(
                            "GOLD V14.4 M15 NEUTRAL -> "
                            "M5 SELL",
                            f"STRONG={strong_sell}",
                            flush=True
                        )


                    else:

                        print(
                            "GOLD V14.4 WAIT: "
                            "M15 NEUTRAL + "
                            "M5 NO CLEAN DIRECTION",
                            flush=True
                        )

                        await asyncio.sleep(
                            LOOP_SECONDS
                        )

                        continue


                # =====================================
                # M15 BUY
                # =====================================

                elif direction == "BUY":

                    # ---------------------------------
                    # NORMAL ALIGNMENT
                    # ---------------------------------

                    if m5_buy_ok:

                        m5_ok = True

                        m5_close = (
                            m5_buy_close
                        )

                        m5_ema9 = (
                            m5_buy_ema9
                        )

                        m5_ema20 = (
                            m5_buy_ema20
                        )


                    # ---------------------------------
                    # STRONG SELL OVERRIDES OLD BUY M15
                    # ---------------------------------

                    elif (
                        M5_REVERSAL_OVERRIDE_ENABLED
                        and
                        strong_sell
                    ):

                        direction = "SELL"

                        m5_ok = True

                        m5_close = (
                            strong_sell_close
                        )

                        m5_ema9 = (
                            strong_sell_ema9
                        )

                        m5_ema20 = (
                            strong_sell_ema20
                        )

                        m15_mode = (
                            f"{original_m15_mode}"
                            "_OVERRIDE_STRONG_M5_SELL"
                        )

                        print(
                            "GOLD V14.4 M15 OVERRIDE:",
                            f"{original_m15_mode} -> "
                            "STRONG M5 SELL",
                            f"BODY={strong_sell_body:.2f}",
                            flush=True
                        )


                    else:

                        print(
                            "GOLD V14.4 WAIT: "
                            "M5 NOT CONFIRMED BUY",
                            f"M15_MODE={m15_mode}",
                            f"STRONG_SELL={strong_sell}",
                            flush=True
                        )

                        await asyncio.sleep(
                            LOOP_SECONDS
                        )

                        continue


                # =====================================
                # M15 SELL
                # =====================================

                elif direction == "SELL":

                    # ---------------------------------
                    # NORMAL ALIGNMENT
                    # ---------------------------------

                    if m5_sell_ok:

                        m5_ok = True

                        m5_close = (
                            m5_sell_close
                        )

                        m5_ema9 = (
                            m5_sell_ema9
                        )

                        m5_ema20 = (
                            m5_sell_ema20
                        )


                    # ---------------------------------
                    # STRONG BUY OVERRIDES OLD SELL M15
                    # ---------------------------------

                    elif (
                        M5_REVERSAL_OVERRIDE_ENABLED
                        and
                        strong_buy
                    ):

                        direction = "BUY"

                        m5_ok = True

                        m5_close = (
                            strong_buy_close
                        )

                        m5_ema9 = (
                            strong_buy_ema9
                        )

                        m5_ema20 = (
                            strong_buy_ema20
                        )

                        m15_mode = (
                            f"{original_m15_mode}"
                            "_OVERRIDE_STRONG_M5_BUY"
                        )

                        print(
                            "GOLD V14.4 M15 OVERRIDE:",
                            f"{original_m15_mode} -> "
                            "STRONG M5 BUY",
                            f"BODY={strong_buy_body:.2f}",
                            flush=True
                        )


                    else:

                        print(
                            "GOLD V14.4 WAIT: "
                            "M5 NOT CONFIRMED SELL",
                            f"M15_MODE={m15_mode}",
                            f"STRONG_BUY={strong_buy}",
                            flush=True
                        )

                        await asyncio.sleep(
                            LOOP_SECONDS
                        )

                        continue


                # =====================================
                # SAFETY
                # =====================================

                else:

                    print(
                        "GOLD V14.4 WAIT: "
                        "NO VALID DIRECTION",
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                if not m5_ok:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # M1 MUST CONFIRM FINAL DIRECTION
                #
                # Toto plati aj pri M15 OVERRIDE.
                # Strong M5 sam obchod neotvori.
                # =====================================

                (
                    trigger_ok,
                    signal_close,
                    trigger_mode,
                    m1_ema9,
                    m1_ema20
                ) = m1_scalp_trigger(
                    candles,
                    direction,
                    atr
                )

                if not trigger_ok:

                    print(
                        "GOLD V14.4 WAIT: "
                        "M1 SCALP RETEST NOT READY",
                        direction,
                        f"M15_MODE={m15_mode}",
                        f"EMA9={m1_ema9}",
                        f"EMA20={m1_ema20}",
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # OLD SAME-DIRECTION SETUP
                # =====================================

                if same_setup_blocked(
                    state,
                    direction
                ):

                    print(
                        "GOLD V14.4 SIGNAL BLOCKED: "
                        "OLD SAME-DIRECTION SETUP",
                        direction,
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # SL SWING
                # =====================================

                (
                    sl_anchor,
                    sl_anchor_mode
                ) = recent_protective_swing(
                    candles,
                    direction
                )

                if sl_anchor is None:

                    print(
                        "GOLD V14.4 WAIT: "
                        "NO SL SWING",
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # UNIQUE SIGNAL
                # =====================================

                signal_key = (
                    f"{candle_time}:"
                    f"{direction}:"
                    f"{trigger_mode}:"
                    f"{round(signal_close, 2)}:"
                    f"{round(sl_anchor, 2)}"
                )

                if (
                    signal_key
                    ==
                    state[
                        "last_signal"
                    ]
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                state[
                    "last_signal"
                ] = signal_key

                save_state(
                    state
                )


                # =====================================
                # SETUP READY
                # =====================================

                print(
                    "GOLD V14.4 SETUP READY:",
                    f"SIDE={direction}",
                    f"M15_ORIGINAL={original_m15_direction}",
                    f"M15_MODE={m15_mode}",
                    f"M15_CLOSE={m15_close}",
                    f"M15_E20={m15_ema20}",
                    f"M15_E50={m15_ema50}",
                    f"M5_CLOSE={m5_close}",
                    f"M5_E9={m5_ema9}",
                    f"M5_E20={m5_ema20}",
                    f"M1_E9={m1_ema9}",
                    f"M1_E20={m1_ema20}",
                    f"M1={trigger_mode}",
                    f"ATR={atr:.2f}",
                    flush=True
                )


                # =====================================
                # OPEN FAST BATCH
                # =====================================

                await open_batch(
                    connection,
                    direction,
                    signal_close,
                    trigger_mode,
                    m15_mode,
                    m1_ema9,
                    m1_ema20,
                    sl_anchor,
                    sl_anchor_mode,
                    atr,
                    state
                )

                await asyncio.sleep(
                    LOOP_SECONDS
                )


            # =========================================
            # METAAPI TEMP ERROR
            # =========================================

            except MetaApiTemporaryError as e:

                consecutive_rpc_failures += 1

                print(
                    "METAAPI TEMPORARY RPC FAILURE "
                    f"{consecutive_rpc_failures}/"
                    f"{MAX_CONSECUTIVE_RPC_FAILURES}: "
                    f"{e}",
                    flush=True
                )

                if (
                    consecutive_rpc_failures
                    >=
                    MAX_CONSECUTIVE_RPC_FAILURES
                ):

                    raise RuntimeError(
                        "METAAPI CONNECTION LOST "
                        "AFTER REPEATED RPC FAILURES"
                    ) from e

                await asyncio.sleep(
                    RPC_FAILURE_WAIT
                )


            # =========================================
            # OTHER ERROR
            # =========================================

            except Exception as e:

                print(
                    "LOOP ERROR:",
                    traceback.format_exc(),
                    flush=True
                )

                message = str(
                    e
                ).lower()

                connection_error = any(
                    word in message
                    for word in (
                        "not connected",
                        "not synchronized",
                        "websocket",
                        "timed out",
                        "timeout",
                        "subscription",
                        "failed to subscribe",
                        "disconnected",
                        "broker yet",
                        "connection lost"
                    )
                )

                if connection_error:

                    consecutive_rpc_failures += 1

                    if (
                        consecutive_rpc_failures
                        >=
                        MAX_CONSECUTIVE_RPC_FAILURES
                    ):

                        raise RuntimeError(
                            "METAAPI CONNECTION LOST "
                            "AFTER REPEATED FAILURES"
                        ) from e

                    await asyncio.sleep(
                        RPC_FAILURE_WAIT
                    )

                    continue

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

    if (
        not M_TOKEN
        or
        not M_ACC
    ):

        telegram(
            "RIO GOLD V14.4 ERROR\n"
            "M_TOKEN OR M_ACC MISSING"
        )

        return

    state = load_state()

    telegram(
        "RIOBOT GOLD V14.4 START\n"
        "STRONG M5 REVERSAL OVERRIDE\n"
        "ADAPTIVE M15\n"
        "STRICT SCALP\n"
        "FAST 12-POSITION BATCH\n"
        "NO CHASE PRE-BATCH ONLY\n"
        "M15 STRICT TREND\n"
        "M15 TRANSITION MODE\n"
        "M15 NEUTRAL -> M5 FALLBACK\n"
        "OLD M15 -> STRONG M5 OVERRIDE\n"
        "M5 EMA9/20 CONFIRMATION\n"
        "M5 STRONG BODY + HIGH/LOW BREAK\n"
        "M1 EMA9/20 RETEST + CONFIRM\n"
        "COMMON SL + COMMON TP LEVELS\n"
        "FRESH M1 FIX ACTIVE\n"
        "NEWS FILTER: OFF\n"
        "MANUAL XAUUSD PROTECTION ACTIVE\n"
        "FRESH SETUP RESET ACTIVE\n"
        "SAME-DIRECTION REENTRY PROTECTION ACTIVE\n"
        f"POST-BATCH COOLDOWN: {COOLDOWN_SECONDS}s\n"
        "CONTINUOUS MODE\n"
        "12 POSITIONS / ONE SIGNAL\n"
        f"LOT EACH: {LOT_SIZE}\n"
        f"TOTAL BATCH: "
        f"{LOT_SIZE * BATCH_SIZE:.2f} LOT\n"
        "TP1: 3 x 0.40R\n"
        "TP2: 3 x 0.70R\n"
        "TP3: 3 x 1.00R\n"
        "TP4: 3 x 1.30R\n"
        "BE1: 0.30R -> +0.05R\n"
        "BE2: 0.60R -> +0.25R\n"
        "BE3: 0.90R -> +0.50R\n"
        f"SL MIN: {MIN_SL_DISTANCE:.2f}\n"
        f"SL MAX: {MAX_SL_DISTANCE:.2f}\n"
        f"MAX SPREAD: {MAX_SPREAD:.2f}\n"
        f"LIVE: {ENABLE_TRADING}"
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
                "RIO GOLD V14.4 CONNECTION ERROR\n"
                f"{type(e).__name__}: "
                f"{str(e)[:150]}\n"
                f"RECONNECT IN "
                f"{RECONNECT_SECONDS} SECONDS"
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
