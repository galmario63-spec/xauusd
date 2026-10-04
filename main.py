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
# RIOBOT GOLD V13.3 - RETEST + COMMON SL/TP
#
# M15 -> M5 -> M1
# M1 EMA20 RETEST + CONFIRMATION
#
# 9 POSITIONS
# 0.30 LOT EACH
# TOTAL 2.70 LOT
#
# 3 x TP1 = 0.80R
# 3 x TP2 = 1.30R
# 3 x TP3 = 2.00R
#
# ONE COMMON SL FOR WHOLE BATCH
# ONE EXACT TP PRICE FOR EACH TP GROUP
#
# SL:
# M1 SWING + 1.50 ATR
# MIN 4.00
# MAX 8.00
#
# BE1 0.35R -> +0.05R
# BE2 0.70R -> +0.30R
# BE3 TP3 1.30R -> +0.80R
# =====================================================

VERSION = "V13.3 COMMON LEVELS"

SYMBOL = "XAUUSD"

COMMENT_PREFIX = "RIOGOLDV133"

MANAGED_COMMENT_PREFIXES = (
    "RIOGOLDV133",
    "RIOGOLDV132",
    "RIOGOLDV13"
)


# =====================================================
# BATCH
# =====================================================

LOT_SIZE = 0.30

BATCH_SIZE = 9
MAX_TRADES = 9

TP1_COUNT = 3
TP2_COUNT = 3
TP3_COUNT = 3

TP1_RR = 0.80
TP2_RR = 1.30
TP3_RR = 2.00


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

COOLDOWN_SECONDS = 180


# =====================================================
# DATA
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
# ATR / TREND
# =====================================================

ATR_PERIOD = 14

M15_EMA_PERIOD = 20
M5_EMA_PERIOD = 20
M1_EMA_PERIOD = 20


# =====================================================
# M1 RETEST
# =====================================================

M1_RETEST_LOOKBACK = 4

M1_RETEST_TOLERANCE_ATR = 0.35

M1_MAX_EMA_DISTANCE_ATR = 0.75

M1_MIN_BODY_RATIO = 0.25

M1_MIN_WICK_RATIO = 0.15

MAX_M1_RANGE_ATR = 2.20

# Potvrdzujúca M1 musí ukázať
# aspoň malý reálny pohyb v smere obchodu.
M1_CONFIRM_MOVE_ATR = 0.05


# =====================================================
# ENTRY DRIFT
# =====================================================

MAX_FORWARD_DRIFT_ATR = 0.55
MAX_ADVERSE_DRIFT_ATR = 0.35


# =====================================================
# SPREAD
# =====================================================

MAX_SPREAD = 0.50
MAX_SPREAD_ATR = 1.20


# =====================================================
# SL
# =====================================================

SWING_LEFT = 2
SWING_RIGHT = 2

SL_SWING_LOOKBACK = 20
SL_FALLBACK_BARS = 12

# Širší ochranný priestor pod/nad swingom
SL_SWING_BUFFER_ATR = 1.50

# Predtým 3.50
MIN_SL_DISTANCE = 4.00

MAX_SL_DISTANCE = 8.00


# =====================================================
# BREAK EVEN
# =====================================================

BE1_TRIGGER_RR = 0.35
BE1_LOCK_RR = 0.05

BE2_TRIGGER_RR = 0.70
BE2_LOCK_RR = 0.30

# LEN TP3 RUNNER
BE3_TRIGGER_RR = 1.30
BE3_LOCK_RR = 0.80


# =====================================================
# FRESH SETUP
# =====================================================

NEW_SETUP_RELEASE_ATR = 0.70


# =====================================================
# BROKER BREAK
# =====================================================

BROKER_BREAK_HOUR_UTC = 22
BROKER_BREAK_MINUTE_UTC = 0

BREAK_BLOCK_BEFORE_MINUTES = 15


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


# =====================================================
# STATE
# =====================================================

STATE_DIR = os.getenv(
    "RIO_STATE_DIR",
    "/tmp"
)

STATE_FILE = os.path.join(
    STATE_DIR,
    "rio_gold_v133.json"
)


# =====================================================
# MARKET DATA CACHE
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
        "RIOBOT GOLD V13.3 COMMON LEVELS ACTIVE",
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
            "NEW GOLD V13.3 STATE",
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

    comment = position_comment(
        position
    )

    return any(
        comment.startswith(prefix)
        for prefix
        in MANAGED_COMMENT_PREFIXES
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
            "GOLD V13.3 CURRENT M1:",
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

            # IBA ZAVRETA M1
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

    return sorted(
        {
            candle["time"]:
                candle
            for candle in candles
        }.values(),
        key=lambda candle:
            candle["time"]
    )


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
            "GOLD V13.3 HISTORICAL STALE "
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
        "GOLD V13.3 M1 DATA:",
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
# BUILD TF
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
            dt.minute % minutes
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
        candle
        for candle
        in groups.values()
        if candle["count"] == minutes
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
            * multiplier
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

    emas = ema_series(
        closes,
        period
    )

    if not emas:
        return None

    return emas[-1]


# =====================================================
# M15 DIRECTION
# =====================================================

def m15_direction(candles):

    m15 = build_tf_candles(
        candles,
        15
    )

    if (
        len(m15)
        <
        M15_EMA_PERIOD + 2
    ):

        return (
            None,
            None,
            None
        )

    closes = [
        candle["close"]
        for candle in m15
    ]

    emas = ema_series(
        closes,
        M15_EMA_PERIOD
    )

    if len(emas) < 2:

        return (
            None,
            None,
            None
        )

    close_now = closes[-1]

    ema_now = emas[-1]
    ema_previous = emas[-2]

    if (
        close_now > ema_now
        and
        ema_now >= ema_previous
    ):

        return (
            "BUY",
            close_now,
            ema_now
        )

    if (
        close_now < ema_now
        and
        ema_now <= ema_previous
    ):

        return (
            "SELL",
            close_now,
            ema_now
        )

    return (
        None,
        close_now,
        ema_now
    )


# =====================================================
# M5 CONFIRMATION
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
        M5_EMA_PERIOD + 2
    ):

        return (
            False,
            None,
            None
        )

    closes = [
        candle["close"]
        for candle in m5
    ]

    emas = ema_series(
        closes,
        M5_EMA_PERIOD
    )

    if len(emas) < 2:

        return (
            False,
            None,
            None
        )

    close_now = closes[-1]

    ema_now = emas[-1]
    ema_previous = emas[-2]

    if side == "BUY":

        allowed = (
            close_now > ema_now
            and
            ema_now >= ema_previous
        )

    else:

        allowed = (
            close_now < ema_now
            and
            ema_now <= ema_previous
        )

    return (
        allowed,
        close_now,
        ema_now
    )


# =====================================================
# M1 RETEST + CONFIRMATION
# =====================================================

def m1_trigger(
    candles,
    side,
    atr
):

    if (
        len(candles)
        <
        M1_EMA_PERIOD
        +
        M1_RETEST_LOOKBACK
        +
        2
    ):

        return (
            False,
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

    if current_range <= 0:

        return (
            False,
            None,
            None,
            None
        )

    if (
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
            None
        )

    ema20 = last_ema(
        candles,
        M1_EMA_PERIOD
    )

    if ema20 is None:

        return (
            False,
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

    confirm_move = (
        atr
        *
        M1_CONFIRM_MOVE_ATR
    )

    recent_pullback = candles[
        -(
            M1_RETEST_LOOKBACK
            + 1
        ):
        -1
    ]

    if not recent_pullback:

        return (
            False,
            None,
            None,
            ema20
        )


    # =================================================
    # BUY
    # =================================================

    if side == "BUY":

        retest_found = any(
            candle["low"]
            <=
            ema20
            +
            tolerance
            for candle
            in recent_pullback
        )

        if not retest_found:

            return (
                False,
                None,
                None,
                ema20
            )

        bullish_confirmation = (
            current["close"]
            >
            current["open"]
            and
            current["close"]
            >
            previous["close"]
            +
            confirm_move
            and
            current["close"]
            >
            ema20
        )

        wick_rejection = (
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
            and
            current["close"]
            >
            ema20
        )

        if not (
            bullish_confirmation
            or
            wick_rejection
        ):

            return (
                False,
                None,
                None,
                ema20
            )

        distance_from_ema = (
            current["close"]
            -
            ema20
        )

        if (
            distance_from_ema
            >
            atr
            *
            M1_MAX_EMA_DISTANCE_ATR
        ):

            return (
                False,
                None,
                None,
                ema20
            )

        return (
            True,
            current["close"],
            "M1 BUY RETEST CONFIRMED",
            ema20
        )


    # =================================================
    # SELL
    # =================================================

    if side == "SELL":

        retest_found = any(
            candle["high"]
            >=
            ema20
            -
            tolerance
            for candle
            in recent_pullback
        )

        if not retest_found:

            return (
                False,
                None,
                None,
                ema20
            )

        bearish_confirmation = (
            current["close"]
            <
            current["open"]
            and
            current["close"]
            <
            previous["close"]
            -
            confirm_move
            and
            current["close"]
            <
            ema20
        )

        wick_rejection = (
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
            and
            current["close"]
            <
            ema20
        )

        if not (
            bearish_confirmation
            or
            wick_rejection
        ):

            return (
                False,
                None,
                None,
                ema20
            )

        distance_from_ema = (
            ema20
            -
            current["close"]
        )

        if (
            distance_from_ema
            >
            atr
            *
            M1_MAX_EMA_DISTANCE_ATR
        ):

            return (
                False,
                None,
                None,
                ema20
            )

        return (
            True,
            current["close"],
            "M1 SELL RETEST CONFIRMED",
            ema20
        )

    return (
        False,
        None,
        None,
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
        -(SL_FALLBACK_BARS + 1):
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
                for candle
                in fallback
            ),
            "FALLBACK_M1_LOW"
        )

    return (
        max(
            candle["high"]
            for candle
            in fallback
        ),
        "FALLBACK_M1_HIGH"
    )


# =====================================================
# FRESH SETUP
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

    last_close = candles[-1][
        "close"
    ]

    release_distance = (
        atr
        *
        NEW_SETUP_RELEASE_ATR
    )

    released = False

    if side == "BUY":

        if (
            last_close
            >=
            reference
            +
            release_distance
        ):

            released = True

    elif side == "SELL":

        if (
            last_close
            <=
            reference
            -
            release_distance
        ):

            released = True

    if released:

        state[
            "setup_released"
        ] = True

        save_state(
            state
        )

        notify(
            "RIO GOLD V13.3 OLD SETUP RELEASED\n"
            f"SIDE: {side}\n"
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
# BROKER BREAK
# =====================================================

def broker_break_blocked():

    now = datetime.now(
        timezone.utc
    )

    current_minutes = (
        now.hour
        * 60
        +
        now.minute
    )

    break_minutes = (
        BROKER_BREAK_HOUR_UTC
        * 60
        +
        BROKER_BREAK_MINUTE_UTC
    )

    start_block = (
        break_minutes
        -
        BREAK_BLOCK_BEFORE_MINUTES
    )

    return (
        start_block
        <=
        current_minutes
        <
        break_minutes
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

    if spread > MAX_SPREAD:

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
# ENTRY DRIFT
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

def tp_group_for_order(number):

    if number <= TP1_COUNT:

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

    return (
        "TP3",
        TP3_RR
    )


# =====================================================
# COMMON BATCH LEVELS
# =====================================================

def build_batch_plan(
    side,
    entry,
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

    minimum_risk = max(
        MIN_SL_DISTANCE,
        min_broker_distance
        +
        market["tick"]
    )


    # =================================================
    # BUY
    # =================================================

    if side == "BUY":

        structural_sl = (
            anchor
            -
            structural_buffer
        )

        structural_risk = (
            entry
            -
            structural_sl
        )

        risk = max(
            structural_risk,
            minimum_risk
        )

        if risk > MAX_SL_DISTANCE:

            return (
                None,
                (
                    "BUY SL TOO LARGE "
                    f"RISK={risk:.2f} "
                    f"MAX={MAX_SL_DISTANCE:.2f}"
                )
            )

        common_sl = normalize(
            entry
            -
            risk,
            market
        )

        actual_risk = (
            entry
            -
            common_sl
        )

        tp1 = normalize(
            entry
            +
            actual_risk
            *
            TP1_RR,
            market
        )

        tp2 = normalize(
            entry
            +
            actual_risk
            *
            TP2_RR,
            market
        )

        tp3 = normalize(
            entry
            +
            actual_risk
            *
            TP3_RR,
            market
        )

        if (
            common_sl
            >=
            market["bid"]
        ):

            return (
                None,
                "INVALID COMMON BUY SL"
            )


    # =================================================
    # SELL
    # =================================================

    else:

        structural_sl = (
            anchor
            +
            structural_buffer
        )

        structural_risk = (
            structural_sl
            -
            entry
        )

        risk = max(
            structural_risk,
            minimum_risk
        )

        if risk > MAX_SL_DISTANCE:

            return (
                None,
                (
                    "SELL SL TOO LARGE "
                    f"RISK={risk:.2f} "
                    f"MAX={MAX_SL_DISTANCE:.2f}"
                )
            )

        common_sl = normalize(
            entry
            +
            risk,
            market
        )

        actual_risk = (
            common_sl
            -
            entry
        )

        tp1 = normalize(
            entry
            -
            actual_risk
            *
            TP1_RR,
            market
        )

        tp2 = normalize(
            entry
            -
            actual_risk
            *
            TP2_RR,
            market
        )

        tp3 = normalize(
            entry
            -
            actual_risk
            *
            TP3_RR,
            market
        )

        if (
            common_sl
            <=
            market["ask"]
        ):

            return (
                None,
                "INVALID COMMON SELL SL"
            )


    if actual_risk <= 0:

        return (
            None,
            "INVALID COMMON RISK"
        )


    return {
        "reference_entry": entry,

        "sl": common_sl,

        "risk": actual_risk,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3
    }, None


# =====================================================
# FIXED LEVEL VALIDATION
# =====================================================

def fixed_levels_valid(
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
# VERIFY POSITION
# =====================================================

async def verify_new_position(
    connection,
    previous_ids,
    side
):

    for attempt in range(8):

        await asyncio.sleep(1)

        try:

            positions = await get_positions(
                connection
            )

        except MetaApiTemporaryError:

            continue

        matches = [
            position
            for position
            in positions
            if (
                str(
                    position["id"]
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
                and
                abs(
                    float(
                        position.get(
                            "volume",
                            0
                        )
                    )
                    -
                    LOT_SIZE
                )
                <
                0.000001
            )
        ]

        if len(matches) == 1:

            return matches[0]

        if len(matches) > 1:

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
    signal_close,
    trigger_mode,
    m1_ema,
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
            "GOLD V13.3 BLOCKED: "
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
            "GOLD V13.3 WAIT: "
            "XAUUSD POSITION ALREADY OPEN",
            len(existing_positions),
            flush=True
        )

        return


    # =================================================
    # FIRST MARKET PRICE
    # =================================================

    first_market = await get_market(
        connection
    )

    (
        spread_ok,
        spread,
        spread_reason
    ) = spread_is_safe(
        first_market,
        atr
    )

    if not spread_ok:

        print(
            "GOLD V13.3 ENTRY BLOCKED:",
            spread_reason,
            f"SPREAD={spread:.2f}",
            flush=True
        )

        return


    batch_entry = (
        first_market["ask"]
        if side == "BUY"
        else first_market["bid"]
    )


    # =================================================
    # LIVE ENTRY
    # =================================================

    (
        live_ok,
        live_reason
    ) = validate_live_entry(
        side,
        batch_entry,
        signal_close,
        atr
    )

    if not live_ok:

        print(
            "GOLD V13.3 ENTRY BLOCKED:",
            live_reason,
            flush=True
        )

        return


    # =================================================
    # EMA DISTANCE
    # =================================================

    if side == "BUY":

        ema_distance = (
            batch_entry
            -
            m1_ema
        )

    else:

        ema_distance = (
            m1_ema
            -
            batch_entry
        )

    if (
        ema_distance
        >
        atr
        *
        M1_MAX_EMA_DISTANCE_ATR
    ):

        print(
            "GOLD V13.3 ENTRY BLOCKED: "
            "TOO FAR FROM M1 EMA20",
            f"DIST={ema_distance:.2f}",
            flush=True
        )

        return


    # =================================================
    # SL ANCHOR
    # =================================================

    if (
        side == "BUY"
        and
        sl_anchor >= batch_entry
    ):

        print(
            "GOLD V13.3 BLOCKED: "
            "BUY SL ANCHOR INVALID",
            flush=True
        )

        return


    if (
        side == "SELL"
        and
        sl_anchor <= batch_entry
    ):

        print(
            "GOLD V13.3 BLOCKED: "
            "SELL SL ANCHOR INVALID",
            flush=True
        )

        return


    # =================================================
    # CREATE ONE COMMON PLAN
    # =================================================

    plan, reason = build_batch_plan(
        side,
        batch_entry,
        sl_anchor,
        atr,
        first_market
    )

    if plan is None:

        print(
            "GOLD V13.3 ENTRY BLOCKED BY SL:",
            reason,
            flush=True
        )

        return


    common_sl = plan[
        "sl"
    ]

    common_tp1 = plan[
        "tp1"
    ]

    common_tp2 = plan[
        "tp2"
    ]

    common_tp3 = plan[
        "tp3"
    ]


    # =================================================
    # LOT CHECK
    # =================================================

    spec = first_market[
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
        LOT_SIZE < min_volume
        or
        volume_step <= 0
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
            "RIO GOLD V13.3 INVALID LOT"
        )

        return


    if not ENABLE_TRADING:

        notify(
            "RIO GOLD V13.3 TEST SIGNAL\n"
            f"SIDE: {side}\n"
            f"M1: {trigger_mode}\n"
            f"ENTRY: {batch_entry:.2f}\n"
            f"COMMON SL: {common_sl:.2f}\n"
            f"TP1: {common_tp1:.2f}\n"
            f"TP2: {common_tp2:.2f}\n"
            f"TP3: {common_tp3:.2f}\n"
            f"RISK: {plan['risk']:.2f}"
        )

        return


    # =================================================
    # LOCK SETUP
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


    notify(
        "RIO GOLD V13.3 ENTRY APPROVED\n"
        f"SIDE: {side}\n"
        "M15: OK\n"
        "M5: OK\n"
        f"M1: {trigger_mode}\n"
        f"M1 EMA20: {m1_ema:.2f}\n"
        f"BATCH ENTRY REF: {batch_entry:.2f}\n"
        f"SL ANCHOR: {sl_anchor:.2f}\n"
        f"SL MODE: {sl_anchor_mode}\n"
        f"COMMON SL: {common_sl:.2f}\n"
        f"TP1 EXACT: {common_tp1:.2f}\n"
        f"TP2 EXACT: {common_tp2:.2f}\n"
        f"TP3 EXACT: {common_tp3:.2f}\n"
        f"RISK: {plan['risk']:.2f}\n"
        f"ATR: {atr:.2f}\n"
        f"SPREAD: {spread:.2f}\n"
        "OPENING 9 x 0.30"
    )


    previous_ids = set()


    try:

        positions = await get_positions(
            connection
        )

        previous_ids = {
            str(
                position["id"]
            )
            for position
            in positions
        }

        if previous_ids:

            raise RuntimeError(
                "POSITION APPEARED BEFORE BATCH"
            )


        # =================================================
        # OPEN 9 POSITIONS
        # =================================================

        for number in range(
            1,
            BATCH_SIZE + 1
        ):

            market = await get_market(
                connection
            )

            (
                spread_ok,
                current_spread,
                spread_reason
            ) = spread_is_safe(
                market,
                atr
            )

            if not spread_ok:

                raise RuntimeError(
                    f"{spread_reason} "
                    f"SPREAD={current_spread:.2f}"
                )


            current_entry = (
                market["ask"]
                if side == "BUY"
                else market["bid"]
            )


            # Cena nesmie utekať počas batchu
            (
                live_ok,
                live_reason
            ) = validate_live_entry(
                side,
                current_entry,
                signal_close,
                atr
            )

            if not live_ok:

                raise RuntimeError(
                    live_reason
                )


            (
                tp_group,
                tp_rr
            ) = tp_group_for_order(
                number
            )


            if tp_group == "TP1":

                fixed_tp = (
                    common_tp1
                )

            elif tp_group == "TP2":

                fixed_tp = (
                    common_tp2
                )

            else:

                fixed_tp = (
                    common_tp3
                )


            (
                levels_ok,
                levels_reason
            ) = fixed_levels_valid(
                side,
                common_sl,
                fixed_tp,
                market
            )


            if not levels_ok:

                raise RuntimeError(
                    levels_reason
                )


            options = {
                "comment":
                    f"{COMMENT_PREFIX}_{tp_group}"
            }


            if side == "BUY":

                result = await meta_call(
                    lambda:
                    connection
                    .create_market_buy_order(
                        SYMBOL,
                        LOT_SIZE,
                        common_sl,
                        fixed_tp,
                        options
                    ),
                    timeout=ORDER_TIMEOUT
                )


            else:

                result = await meta_call(
                    lambda:
                    connection
                    .create_market_sell_order(
                        SYMBOL,
                        LOT_SIZE,
                        common_sl,
                        fixed_tp,
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
                position[
                    "id"
                ]
            )

            previous_ids.add(
                pid
            )


            actual_entry = float(
                position.get(
                    "openPrice"
                )
                or
                current_entry
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
                or
                actual_tp <= 0
            ):

                raise RuntimeError(
                    "BROKER SL OR TP MISSING"
                )


            actual_risk = abs(
                actual_entry
                -
                actual_sl
            )


            if actual_risk <= 0:

                raise RuntimeError(
                    "INVALID ACTUAL RISK"
                )


            state[
                "positions"
            ][pid] = {

                "risk":
                    actual_risk,

                "entry":
                    actual_entry,

                "side":
                    side,

                "tp_group":
                    tp_group,

                "tp_rr":
                    tp_rr
            }


            state[
                "last_trade_time"
            ] = time.time()


            save_state(
                state
            )


            notify(
                "RIO GOLD V13.3 ORDER "
                f"{number}/9 OK\n"
                f"GROUP: {tp_group}\n"
                f"SIDE: {side}\n"
                f"LOT: {LOT_SIZE}\n"
                f"ENTRY: {actual_entry}\n"
                f"COMMON SL: {actual_sl}\n"
                f"GROUP TP: {actual_tp}\n"
                f"RISK: {actual_risk:.2f}"
            )


        state[
            "order_uncertain"
        ] = False

        state[
            "halted"
        ] = False

        save_state(
            state
        )


        notify(
            "RIO GOLD V13.3 BATCH COMPLETED\n"
            "9/9 OPENED\n"
            "9 x 0.30 LOT\n"
            "TOTAL: 2.70 LOT\n"
            f"COMMON SL: {common_sl:.2f}\n"
            f"TP1: {common_tp1:.2f}\n"
            f"TP2: {common_tp2:.2f}\n"
            f"TP3: {common_tp3:.2f}\n"
            "BE1: 0.35R -> +0.05R\n"
            "BE2: 0.70R -> +0.30R\n"
            "BE3 TP3: 1.30R -> +0.80R"
        )


    except Exception as e:

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
            "RIO GOLD V13.3 BATCH STOPPED\n"
            f"ERROR: {type(e).__name__}\n"
            f"{str(e)[:180]}"
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
        position[
            "id"
        ]
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
        info[
            "risk"
        ]
    )

    entry = float(
        position[
            "openPrice"
        ]
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


    group = str(
        info.get(
            "tp_group",
            ""
        )
    ).upper()


    # =================================================
    # BE3 - TP3 ONLY
    # =================================================

    if (
        group == "TP3"
        and
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
        f"RIO GOLD V13.3 {stage} ACTIVE\n"
        f"POSITION: {pid}\n"
        f"SIDE: {side}\n"
        f"GROUP: {group}\n"
        f"ENTRY: {entry}\n"
        f"RISK: {risk:.2f}\n"
        f"TRIGGER: {trigger_rr:.2f}R\n"
        f"SL LOCK: {wanted_sl}\n"
        f"LOCK: +{lock_rr:.2f}R"
    )


# =====================================================
# ADOPT / RECOVER
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

    return (
        None,
        None
    )


async def adopt_positions(
    connection,
    state
):

    positions = await get_positions(
        connection
    )


    managed = [
        position
        for position
        in positions
        if is_managed_position(
            position
        )
    ]


    adopted = 0


    for position in managed:

        pid = str(
            position[
                "id"
            ]
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


        (
            group,
            rr
        ) = rr_from_comment(
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
            "RIO GOLD V13.3 POSITIONS ADOPTED\n"
            f"BOT OPEN: {len(managed)}/9\n"
            f"TRACKED: "
            f"{len(state['positions'])}/9\n"
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
        for position
        in positions
        if is_managed_position(
            position
        )
    ]


    current_managed_ids = {
        str(
            position[
                "id"
            ]
        )
        for position
        in managed
    }


    known_ids = set(
        state[
            "positions"
        ].keys()
    )


    closed_ids = (
        known_ids
        -
        current_managed_ids
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
            "RIO GOLD V13.3 POSITION CLOSED\n"
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


        notify(
            "RIO GOLD V13.3 CONNECTED\n"
            "RETEST + COMMON LEVELS ACTIVE\n"
            "M15 -> M5 -> M1 ACTIVE\n"
            "M1 EMA20 RETEST ACTIVE\n"
            "M1 CONFIRMATION ACTIVE\n"
            "COMMON SL ACTIVE\n"
            "EXACT TP GROUPS ACTIVE\n"
            "NO CHASE ACTIVE\n"
            "FRESH M1 FIX ACTIVE\n"
            "NEWS FILTER: OFF\n"
            "MANUAL XAUUSD PROTECTION ACTIVE\n"
            "FRESH SETUP PROTECTION ACTIVE\n"
            "9 POSITIONS / ONE SIGNAL\n"
            f"LOT EACH: {LOT_SIZE}\n"
            f"TOTAL BATCH: "
            f"{LOT_SIZE * BATCH_SIZE:.2f} LOT\n"
            "TP1: 3 x 0.80R\n"
            "TP2: 3 x 1.30R\n"
            "TP3: 3 x 2.00R\n"
            "BE1: 0.35R -> +0.05R\n"
            "BE2: 0.70R -> +0.30R\n"
            "BE3 TP3: 1.30R -> +0.80R\n"
            "SL: M1 SWING + 1.50 ATR\n"
            f"MIN SL: {MIN_SL_DISTANCE:.2f}\n"
            f"MAX SL: {MAX_SL_DISTANCE:.2f}\n"
            f"MAX SPREAD: {MAX_SPREAD:.2f}\n"
            f"BOT OPEN: {len(managed)}/9\n"
            f"ALL XAUUSD OPEN: "
            f"{len(all_positions)}\n"
            f"LIVE: {ENABLE_TRADING}"
        )


        consecutive_rpc_failures = 0


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
                # OPEN POSITIONS
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
                        len(all_positions)
                        -
                        len(managed)
                    )


                    if manual_count > 0:

                        print(
                            "GOLD V13.3 WAIT: "
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

                if (
                    time.time()
                    -
                    state[
                        "last_trade_time"
                    ]
                    <
                    COOLDOWN_SECONDS
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # BROKER BREAK
                # =====================================

                if broker_break_blocked():

                    print(
                        "GOLD V13.3 ENTRY BLOCKED: "
                        "BROKER BREAK SOON",
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
                        "GOLD V13.3 WAIT: "
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
                    m15_ema
                ) = m15_direction(
                    candles
                )


                if direction is None:

                    print(
                        "GOLD V13.3 WAIT: "
                        "M15 NO CLEAR DIRECTION",
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # M5
                # =====================================

                (
                    m5_ok,
                    m5_close,
                    m5_ema
                ) = m5_confirmation(
                    candles,
                    direction
                )


                if not m5_ok:

                    print(
                        "GOLD V13.3 WAIT: "
                        "M5 NOT CONFIRMED",
                        direction,
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # M1 RETEST
                # =====================================

                (
                    trigger_ok,
                    signal_close,
                    trigger_mode,
                    m1_ema
                ) = m1_trigger(
                    candles,
                    direction,
                    atr
                )


                if not trigger_ok:

                    print(
                        "GOLD V13.3 WAIT: "
                        "M1 RETEST NOT READY",
                        direction,
                        f"EMA20={m1_ema}",
                        flush=True
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue


                # =====================================
                # OLD SETUP
                # =====================================

                if same_setup_blocked(
                    state,
                    direction
                ):

                    print(
                        "GOLD V13.3 SIGNAL BLOCKED: "
                        "OLD SETUP",
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
                        "GOLD V13.3 WAIT: "
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


                print(
                    "GOLD V13.3 SETUP READY:",
                    f"M15={direction}",
                    f"M15_CLOSE={m15_close}",
                    f"M15_EMA={m15_ema}",
                    f"M5_CLOSE={m5_close}",
                    f"M5_EMA={m5_ema}",
                    f"M1_EMA={m1_ema}",
                    f"M1={trigger_mode}",
                    f"ATR={atr:.2f}",
                    flush=True
                )


                # =====================================
                # OPEN
                # =====================================

                await open_batch(
                    connection,
                    direction,
                    signal_close,
                    trigger_mode,
                    m1_ema,
                    sl_anchor,
                    sl_anchor_mode,
                    atr,
                    state
                )


                await asyncio.sleep(
                    LOOP_SECONDS
                )


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
            "RIO GOLD V13.3 ERROR\n"
            "M_TOKEN OR M_ACC MISSING"
        )

        return


    state = load_state()


    telegram(
        "RIOBOT GOLD V13.3 START\n"
        "COMMON SL / EXACT TP ACTIVE\n"
        "M15 -> M5 -> M1\n"
        "M1 EMA20 RETEST\n"
        "M1 CONFIRMATION REQUIRED\n"
        "FRESH M1 FIX ACTIVE\n"
        "NEWS FILTER: OFF\n"
        "MANUAL XAUUSD PROTECTION ACTIVE\n"
        "FRESH SETUP PROTECTION ACTIVE\n"
        "9 POSITIONS / ONE SIGNAL\n"
        f"LOT EACH: {LOT_SIZE}\n"
        f"TOTAL BATCH: "
        f"{LOT_SIZE * BATCH_SIZE:.2f} LOT\n"
        "TP1: 3 x 0.80R\n"
        "TP2: 3 x 1.30R\n"
        "TP3: 3 x 2.00R\n"
        "BE1: 0.35R -> +0.05R\n"
        "BE2: 0.70R -> +0.30R\n"
        "BE3 TP3: 1.30R -> +0.80R\n"
        "SL: M1 SWING + 1.50 ATR\n"
        f"MIN SL: {MIN_SL_DISTANCE:.2f}\n"
        f"MAX SL: {MAX_SL_DISTANCE:.2f}\n"
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
                "RIO GOLD V13.3 CONNECTION ERROR\n"
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
