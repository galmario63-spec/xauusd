import asyncio
import json
import os
import time
from datetime import datetime
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD - M1 + M5 FROM M1 + BREAKOUT/RETEST
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 8.00
SL_DISTANCE = 10.00

BE1_TRIGGER_DISTANCE = 4.00
BE1_LOCK_DISTANCE = 1.00

BE2_TRIGGER_DISTANCE = 7.00
BE2_LOCK_DISTANCE = 5.00

SIGNAL_CONFIRM_SECONDS = 10

SETUP_EXPIRY_SECONDS = 4 * 60
RETEST_EXPIRY_SECONDS = 2 * 60

IMPULSE_LOOKBACK = 6
IMPULSE_MULTIPLIER = 2.40

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

NEWS_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30
NEWS_FETCH_SECONDS = 30 * 60
NEWS_STALE_SECONDS = 3 * 60 * 60

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6

# Viac M1 dát, aby bolo dosť dát aj na M5.
MAX_CACHE_BARS = 300
HISTORY_SEED_BARS = 180

M1_CACHE_FILE = "m1_cache.json"

COMMENT = "RIO M1 M5 RETEST"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# KEEP ALIVE
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD ACTIVE", 200


def run_server():
    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )


def keep_alive():
    Thread(
        target=run_server,
        daemon=True
    ).start()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):

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
            timeout=10,
        )

    except Exception as exc:

        print(
            f"TELEGRAM WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


# =========================================================
# CACHE
# =========================================================

def load_cache(filename):

    try:

        if not os.path.exists(
            filename
        ):
            return []

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        if not isinstance(
            data,
            list
        ):
            return []

        clean = [
            candle
            for candle
            in data[-MAX_CACHE_BARS:]
            if all(
                key in candle
                for key in (
                    "time",
                    "open",
                    "high",
                    "low",
                    "close"
                )
            )
        ]

        clean.sort(
            key=lambda x:
                str(
                    x["time"]
                )
        )

        return clean

    except Exception as exc:

        print(
            f"CACHE LOAD WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return []


def save_cache(
    candles,
    filename
):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                candles[
                    -MAX_CACHE_BARS:
                ],
                file
            )

    except Exception as exc:

        print(
            f"CACHE SAVE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


def update_candle_cache(
    candles,
    candle,
    filename
):

    if candle is None:
        return

    for i in range(
        len(candles) - 1,
        -1,
        -1
    ):

        if (
            str(
                candles[i][
                    "time"
                ]
            )
            ==
            str(
                candle[
                    "time"
                ]
            )
        ):

            candles[i] = candle

            break

    else:

        candles.append(
            candle
        )

    candles.sort(
        key=lambda x:
            str(
                x["time"]
            )
    )

    del candles[
        :-MAX_CACHE_BARS
    ]

    save_cache(
        candles,
        filename
    )


# =========================================================
# METAAPI M1 DATA
# =========================================================

def clean_candle(
    candle
):

    if not candle:
        return None

    if not all(
        key in candle
        for key in (
            "time",
            "open",
            "high",
            "low",
            "close"
        )
    ):

        return None

    return {
        "time":
            str(
                candle[
                    "time"
                ]
            ),

        "open":
            float(
                candle[
                    "open"
                ]
            ),

        "high":
            float(
                candle[
                    "high"
                ]
            ),

        "low":
            float(
                candle[
                    "low"
                ]
            ),

        "close":
            float(
                candle[
                    "close"
                ]
            ),
    }


async def get_current_m1_candle(
    region
):

    url = (
        f"https://mt-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/"
        f"{M_ACC}/symbols/"
        f"{SYMBOL}/"
        f"current-candles/1m"
        f"?keepSubscription=true"
    )

    def fetch():

        response = requests.get(
            url,
            headers={
                "Accept":
                    "application/json",

                "auth-token":
                    M_TOKEN,
            },
            timeout=20,
        )

        if (
            response.status_code
            != 200
        ):

            raise RuntimeError(
                f"M1 candle HTTP "
                f"{response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:

        raw = (
            await asyncio.to_thread(
                fetch
            )
        )

        return clean_candle(
            raw
        )

    except Exception as exc:

        print(
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        return None


async def get_historical_m1_candles(
    region,
    limit=HISTORY_SEED_BARS
):

    url = (
        f"https://mt-market-data-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/"
        f"{M_ACC}/historical-market-data/"
        f"symbols/{SYMBOL}/"
        f"timeframes/1m/"
        f"candles?limit={limit}"
    )

    def fetch():

        response = requests.get(
            url,
            headers={
                "Accept":
                    "application/json",

                "auth-token":
                    M_TOKEN,
            },
            timeout=40,
        )

        if (
            response.status_code
            != 200
        ):

            raise RuntimeError(
                f"historical M1 HTTP "
                f"{response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:

        raw = (
            await asyncio.to_thread(
                fetch
            )
        )

        candles = []

        if isinstance(
            raw,
            list
        ):

            for item in raw:

                candle = (
                    clean_candle(
                        item
                    )
                )

                if candle:

                    candles.append(
                        candle
                    )

        candles.sort(
            key=lambda x:
                str(
                    x["time"]
                )
        )

        return candles[
            -MAX_CACHE_BARS:
        ]

    except Exception as exc:

        print(
            f"HISTORY M1 WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        return []


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(
    df
):

    highs = (
        df[
            "high"
        ]
        .astype(float)
        .tolist()
    )

    lows = (
        df[
            "low"
        ]
        .astype(float)
        .tolist()
    )

    count = len(
        df
    )

    if count < 3:

        return [
            None
        ] * count

    psar = [
        None
    ] * count

    bull = True

    af = PSAR_STEP

    ep = highs[
        0
    ]

    sar = lows[
        0
    ]

    psar[
        0
    ] = sar

    for i in range(
        1,
        count
    ):

        sar = (
            sar
            +
            af
            *
            (
                ep
                -
                sar
            )
        )

        if bull:

            if i >= 2:

                sar = min(
                    sar,
                    lows[
                        i - 1
                    ],
                    lows[
                        i - 2
                    ]
                )

            else:

                sar = min(
                    sar,
                    lows[
                        i - 1
                    ]
                )

            if (
                lows[
                    i
                ]
                <
                sar
            ):

                bull = False

                sar = ep

                ep = lows[
                    i
                ]

                af = PSAR_STEP

            elif (
                highs[
                    i
                ]
                >
                ep
            ):

                ep = highs[
                    i
                ]

                af = min(
                    af
                    +
                    PSAR_STEP,
                    PSAR_MAX
                )

        else:

            if i >= 2:

                sar = max(
                    sar,
                    highs[
                        i - 1
                    ],
                    highs[
                        i - 2
                    ]
                )

            else:

                sar = max(
                    sar,
                    highs[
                        i - 1
                    ]
                )

            if (
                highs[
                    i
                ]
                >
                sar
            ):

                bull = True

                sar = ep

                ep = highs[
                    i
                ]

                af = PSAR_STEP

            elif (
                lows[
                    i
                ]
                <
                ep
            ):

                ep = lows[
                    i
                ]

                af = min(
                    af
                    +
                    PSAR_STEP,
                    PSAR_MAX
                )

        psar[
            i
        ] = sar

    return psar


# =========================================================
# M1 DATAFRAME
# =========================================================

def make_dataframe(
    candles
):

    if (
        len(
            candles
        )
        <
        MIN_PSAR_BARS
    ):

        return None

    df = pd.DataFrame(
        candles
    )

    for col in (
        "open",
        "high",
        "low",
        "close"
    ):

        df[
            col
        ] = pd.to_numeric(
            df[
                col
            ],
            errors="coerce"
        )

    df = (
        df
        .dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ]
        )
        .reset_index(
            drop=True
        )
    )

    if (
        len(
            df
        )
        <
        MIN_PSAR_BARS
    ):

        return None

    df[
        "psar"
    ] = psar_values(
        df
    )

    return df


# =========================================================
# M5 SA VYTVARA Z M1
# =========================================================

def make_m5_dataframe(
    m1_candles
):

    """
    M5 sviecky sa vytvoria priamo
    z M1 dat.

    Robot uz NEVOLA:
    current-candles/5m

    Tym padom zmizne M5 HTTP 404.
    """

    if (
        len(
            m1_candles
        )
        <
        10
    ):

        return None

    df = pd.DataFrame(
        m1_candles
    )

    for col in (
        "open",
        "high",
        "low",
        "close"
    ):

        df[
            col
        ] = pd.to_numeric(
            df[
                col
            ],
            errors="coerce"
        )

    df[
        "dt"
    ] = pd.to_datetime(
        df[
            "time"
        ],
        utc=True,
        errors="coerce"
    )

    df = (
        df
        .dropna(
            subset=[
                "dt",
                "open",
                "high",
                "low",
                "close"
            ]
        )
        .sort_values(
            "dt"
        )
    )

    if df.empty:
        return None

    df[
        "m5_bucket"
    ] = (
        df[
            "dt"
        ]
        .dt
        .floor(
            "5min"
        )
    )

    m5 = (
        df
        .groupby(
            "m5_bucket",
            as_index=False
        )
        .agg(
            open=(
                "open",
                "first"
            ),

            high=(
                "high",
                "max"
            ),

            low=(
                "low",
                "min"
            ),

            close=(
                "close",
                "last"
            ),
        )
    )

    if (
        len(
            m5
        )
        <
        MIN_PSAR_BARS
    ):

        return None

    m5[
        "time"
    ] = (
        m5[
            "m5_bucket"
        ]
        .dt
        .strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
    )

    m5 = (
        m5[
            [
                "time",
                "open",
                "high",
                "low",
                "close"
            ]
        ]
        .reset_index(
            drop=True
        )
    )

    m5[
        "psar"
    ] = psar_values(
        m5
    )

    return m5


# =========================================================
# HISTORY START
# =========================================================

async def seed_history(
    state
):

    if state.get(
        "history_seeded"
    ):

        return

    history = (
        await get_historical_m1_candles(
            state[
                "meta_region"
            ]
        )
    )

    if history:

        state[
            "m1_candles"
        ] = history

        save_cache(
            history,
            M1_CACHE_FILE
        )

    df_m1 = make_dataframe(
        state[
            "m1_candles"
        ]
    )

    df_m5 = make_m5_dataframe(
        state[
            "m1_candles"
        ]
    )

    state[
        "history_seeded"
    ] = (
        df_m1 is not None
        and
        df_m5 is not None
        and
        len(
            df_m5
        )
        >=
        MIN_PSAR_BARS
    )

    if state[
        "history_seeded"
    ]:

        print(
            f"HISTORY READY: "
            f"M1={len(df_m1)} "
            f"M5={len(df_m5)} "
            f"(M5 derived from M1)",
            flush=True
        )


# =========================================================
# SIGNALS
# =========================================================

def get_live_signal(
    df
):

    if (
        df is None
        or
        len(
            df
        )
        <
        3
    ):

        return None

    previous = df.iloc[
        -2
    ]

    current = df.iloc[
        -1
    ]

    previous_close = float(
        previous[
            "close"
        ]
    )

    current_close = float(
        current[
            "close"
        ]
    )

    previous_psar = float(
        previous[
            "psar"
        ]
    )

    current_psar = float(
        current[
            "psar"
        ]
    )

    # BUY
    if (
        previous_psar
        >
        previous_close
        and
        current_psar
        <
        current_close
    ):

        return "BUY"

    # SELL
    if (
        previous_psar
        <
        previous_close
        and
        current_psar
        >
        current_close
    ):

        return "SELL"

    return None


def get_current_psar_side(
    df
):

    if (
        df is None
        or
        len(
            df
        )
        <
        1
    ):

        return None

    current = df.iloc[
        -1
    ]

    close = float(
        current[
            "close"
        ]
    )

    psar = float(
        current[
            "psar"
        ]
    )

    if psar < close:
        return "BUY"

    if psar > close:
        return "SELL"

    return None


def m5_confirms(
    df_m5,
    side
):

    # 2 M5 PSAR bodky
    # musia byt v rovnakom smere.

    if (
        df_m5 is None
        or
        len(
            df_m5
        )
        <
        2
    ):

        return False

    last_two = (
        df_m5.iloc[
            -2:
        ]
    )

    if side == "BUY":

        return all(
            float(
                row[
                    "psar"
                ]
            )
            <
            float(
                row[
                    "close"
                ]
            )
            for _,
            row
            in last_two.iterrows()
        )

    if side == "SELL":

        return all(
            float(
                row[
                    "psar"
                ]
            )
            >
            float(
                row[
                    "close"
                ]
            )
            for _,
            row
            in last_two.iterrows()
        )

    return False


# =========================================================
# PRICE ACTION FILTERS
# =========================================================

def recent_average_range(
    df
):

    if (
        df is None
        or
        len(
            df
        )
        <
        4
    ):

        return 0.0

    closed = (
        df.iloc[
            :-1
        ]
        .tail(
            IMPULSE_LOOKBACK
        )
    )

    ranges = (
        closed[
            "high"
        ]
        .astype(float)
        -
        closed[
            "low"
        ]
        .astype(float)
    )

    ranges = ranges[
        ranges > 0
    ]

    if ranges.empty:
        return 0.0

    return float(
        ranges.mean()
    )


def is_impulse_candle(
    df
):

    avg_range = recent_average_range(
        df
    )

    if avg_range <= 0:
        return False

    current = df.iloc[
        -1
    ]

    current_range = (
        float(
            current[
                "high"
            ]
        )
        -
        float(
            current[
                "low"
            ]
        )
    )

    return (
        current_range
        >=
        avg_range
        *
        IMPULSE_MULTIPLIER
    )


def setup_parameters(
    df
):

    avg_range = recent_average_range(
        df
    )

    if avg_range <= 0:

        avg_range = 0.80

    return {

        "max_chase":
            min(
                max(
                    avg_range
                    *
                    0.90,
                    0.50
                ),
                1.80
            ),

        "retest_tolerance":
            min(
                max(
                    avg_range
                    *
                    0.30,
                    0.15
                ),
                0.55
            ),

        "invalidation":
            min(
                max(
                    avg_range
                    *
                    0.55,
                    0.30
                ),
                1.00
            ),

        "reentry_confirm":
            min(
                max(
                    avg_range
                    *
                    0.20,
                    0.10
                ),
                0.35
            ),

        "min_extension":
            min(
                max(
                    avg_range
                    *
                    0.35,
                    0.20
                ),
                0.60
            ),
    }


# =========================================================
# POSITIONS
# =========================================================

async def get_positions(
    connection
):

    positions = (
        await asyncio.wait_for(
            connection.get_positions(),
            timeout=META_TIMEOUT
        )
    )

    return [
        position
        for position
        in positions
        if str(
            position.get(
                "symbol",
                ""
            )
        ).upper()
        ==
        SYMBOL.upper()
    ]


# =========================================================
# MARKET
# =========================================================

async def get_market(
    connection
):

    specification = (
        await asyncio.wait_for(
            connection
            .get_symbol_specification(
                SYMBOL
            ),
            timeout=META_TIMEOUT
        )
    )

    price = (
        await asyncio.wait_for(
            connection
            .get_symbol_price(
                SYMBOL
            ),
            timeout=META_TIMEOUT
        )
    )

    digits = int(
        specification.get(
            "digits",
            2
        )
    )

    point = float(
        specification.get(
            "tickSize"
        )
        or
        10 ** (-digits)
    )

    bid = float(
        price[
            "bid"
        ]
    )

    ask = float(
        price[
            "ask"
        ]
    )

    return (
        point,
        digits,
        bid,
        ask
    )


# =========================================================
# EXACT SL / TP
# =========================================================

def exact_levels(
    side,
    entry,
    digits
):

    if side in (
        "BUY",
        "POSITION_TYPE_BUY"
    ):

        return (
            round(
                entry
                -
                SL_DISTANCE,
                digits
            ),

            round(
                entry
                +
                TP_DISTANCE,
                digits
            )
        )

    return (
        round(
            entry
            +
            SL_DISTANCE,
            digits
        ),

        round(
            entry
            -
            TP_DISTANCE,
            digits
        )
    )


async def wait_for_symbol_position(
    connection,
    side,
    attempts=20
):

    for _ in range(
        attempts
    ):

        positions = (
            await get_positions(
                connection
            )
        )

        for position in positions:

            position_side = str(
                position.get(
                    "type",
                    ""
                )
            ).upper()

            if (
                side == "BUY"
                and
                position_side
                in (
                    "BUY",
                    "POSITION_TYPE_BUY"
                )
            ):

                return position

            if (
                side == "SELL"
                and
                position_side
                in (
                    "SELL",
                    "POSITION_TYPE_SELL"
                )
            ):

                return position

        await asyncio.sleep(
            0.25
        )

    return None


async def ensure_exact_stops(
    connection
):

    positions = (
        await get_positions(
            connection
        )
    )

    if not positions:
        return

    (
        _,
        digits,
        _,
        _
    ) = (
        await get_market(
            connection
        )
    )

    epsilon = (
        10
        **
        (-digits)
        /
        2
    )

    for position in positions:

        position_id = (
            position.get(
                "id"
            )
        )

        side = str(
            position.get(
                "type",
                ""
            )
        ).upper()

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or
            0
        )

        if (
            not position_id
            or
            entry <= 0
        ):

            continue

        (
            exact_sl,
            exact_tp
        ) = exact_levels(
            side,
            entry,
            digits
        )

        current_sl_raw = (
            position.get(
                "stopLoss"
            )
        )

        current_tp_raw = (
            position.get(
                "takeProfit"
            )
        )

        current_sl = (
            float(
                current_sl_raw
            )
            if
            current_sl_raw
            not in (
                None,
                0
            )
            else
            None
        )

        current_tp = (
            float(
                current_tp_raw
            )
            if
            current_tp_raw
            not in (
                None,
                0
            )
            else
            None
        )

        # BE sa nikdy nevrati spat.
        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):

            target_sl = (
                current_sl
                if (
                    current_sl
                    is not None
                    and
                    current_sl
                    >=
                    entry
                )
                else
                exact_sl
            )

        elif side in (
            "SELL",
            "POSITION_TYPE_SELL"
        ):

            target_sl = (
                current_sl
                if (
                    current_sl
                    is not None
                    and
                    current_sl
                    <=
                    entry
                )
                else
                exact_sl
            )

        else:

            continue

        needs_sl = (
            current_sl
            is None
            or
            abs(
                current_sl
                -
                target_sl
            )
            >
            epsilon
        )

        needs_tp = (
            current_tp
            is None
            or
            abs(
                current_tp
                -
                exact_tp
            )
            >
            epsilon
        )

        if (
            needs_sl
            or
            needs_tp
        ):

            await asyncio.wait_for(
                connection
                .modify_position(
                    position_id,
                    target_sl,
                    exact_tp
                ),
                timeout=META_TIMEOUT
            )


# =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(
    connection,
    side
):

    (
        _,
        digits,
        bid,
        ask
    ) = (
        await get_market(
            connection
        )
    )

    provisional_entry = (
        ask
        if
        side == "BUY"
        else
        bid
    )

    (
        sl,
        tp
    ) = exact_levels(
        side,
        provisional_entry,
        digits
    )

    if side == "BUY":

        result = (
            await asyncio.wait_for(
                connection
                .create_market_buy_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    {
                        "comment":
                            COMMENT
                    }
                ),
                timeout=META_TIMEOUT
            )
        )

    else:

        result = (
            await asyncio.wait_for(
                connection
                .create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    {
                        "comment":
                            COMMENT
                    }
                ),
                timeout=META_TIMEOUT
            )
        )

    position = (
        await wait_for_symbol_position(
            connection,
            side
        )
    )

    if position:

        position_id = (
            position.get(
                "id"
            )
        )

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or
            0
        )

        (
            sl,
            tp
        ) = exact_levels(
            side,
            entry,
            digits
        )

        await asyncio.wait_for(
            connection
            .modify_position(
                position_id,
                sl,
                tp
            ),
            timeout=META_TIMEOUT
        )

        print(
            f"ORDER OK "
            f"{side} "
            f"ENTRY={entry:.2f} "
            f"SL={sl:.2f} "
            f"TP={tp:.2f}",
            flush=True
        )

        telegram(
            "RIObot GOLD\n\n"
            f"{side} {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            f"Entry: {entry:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP: {tp:.2f}\n"
            "M1 PSAR: 1st DOT + 10s\n"
            "M5: 2 PSAR dots confirm "
            "(derived from M1)\n"
            "ENTRY: breakout -> retest -> confirmation\n"
            "NO CHASE / NO BIG IMPULSE\n"
            "BE1: +4.00 -> +1.00\n"
            "BE2: +7.00 -> +5.00\n"
            "NEWS: HIGH USD -15m / +30m"
        )

    return result


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(
    connection
):

    positions = (
        await get_positions(
            connection
        )
    )

    if not positions:
        return

    (
        _,
        digits,
        bid,
        ask
    ) = (
        await get_market(
            connection
        )
    )

    for position in positions:

        position_id = (
            position.get(
                "id"
            )
        )

        side = str(
            position.get(
                "type",
                ""
            )
        ).upper()

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or
            0
        )

        if (
            not position_id
            or
            entry <= 0
        ):

            continue

        current_sl_raw = (
            position.get(
                "stopLoss"
            )
        )

        current_tp_raw = (
            position.get(
                "takeProfit"
            )
        )

        current_sl = (
            float(
                current_sl_raw
            )
            if
            current_sl_raw
            not in (
                None,
                0
            )
            else
            None
        )

        current_tp = (
            float(
                current_tp_raw
            )
            if
            current_tp_raw
            not in (
                None,
                0
            )
            else
            None
        )

        # BUY
        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):

            profit = (
                bid
                -
                entry
            )

            if (
                profit
                >=
                BE2_TRIGGER_DISTANCE
            ):

                new_sl = round(
                    entry
                    +
                    BE2_LOCK_DISTANCE,
                    digits
                )

                label = "BE2"
                reached = "+7.00"
                locked = "+5.00"

            elif (
                profit
                >=
                BE1_TRIGGER_DISTANCE
            ):

                new_sl = round(
                    entry
                    +
                    BE1_LOCK_DISTANCE,
                    digits
                )

                label = "BE1"
                reached = "+4.00"
                locked = "+1.00"

            else:

                continue

            improve = (
                current_sl
                is None
                or
                current_sl
                <
                new_sl
            )

        # SELL
        elif side in (
            "SELL",
            "POSITION_TYPE_SELL"
        ):

            profit = (
                entry
                -
                ask
            )

            if (
                profit
                >=
                BE2_TRIGGER_DISTANCE
            ):

                new_sl = round(
                    entry
                    -
                    BE2_LOCK_DISTANCE,
                    digits
                )

                label = "BE2"
                reached = "+7.00"
                locked = "+5.00"

            elif (
                profit
                >=
                BE1_TRIGGER_DISTANCE
            ):

                new_sl = round(
                    entry
                    -
                    BE1_LOCK_DISTANCE,
                    digits
                )

                label = "BE1"
                reached = "+4.00"
                locked = "+1.00"

            else:

                continue

            improve = (
                current_sl
                is None
                or
                current_sl
                >
                new_sl
            )

        else:

            continue

        if improve:

            await asyncio.wait_for(
                connection
                .modify_position(
                    position_id,
                    new_sl,
                    current_tp
                ),
                timeout=META_TIMEOUT
            )

            clean_side = (
                side.replace(
                    "POSITION_TYPE_",
                    ""
                )
            )

            telegram(
                f"RIObot GOLD "
                f"{label}\n\n"
                f"{clean_side} "
                f"{SYMBOL}\n"
                f"{reached} reached\n"
                f"SL locked {locked}\n"
                f"SL: {new_sl:.2f}"
            )


# =========================================================
# NEWS
# =========================================================

def parse_news_datetime(
    value
):

    try:

        return (
            datetime
            .fromisoformat(
                str(
                    value
                )
                .strip()
                .replace(
                    "Z",
                    "+00:00"
                )
            )
            .timestamp()
        )

    except Exception:

        return None


async def refresh_news_calendar(
    state,
    force=False
):

    now = time.time()

    if (
        not force
        and
        now
        <
        state.get(
            "news_next_fetch",
            0
        )
    ):

        return

    state[
        "news_next_fetch"
    ] = (
        now
        +
        NEWS_FETCH_SECONDS
    )

    def fetch():

        response = requests.get(
            NEWS_URL,
            headers={
                "Accept":
                    "application/json",

                "User-Agent":
                    "RIObot-GOLD/1.0"
            },
            timeout=20,
        )

        response.raise_for_status()

        return response.json()

    try:

        raw = (
            await asyncio.to_thread(
                fetch
            )
        )

        events = []

        if isinstance(
            raw,
            list
        ):

            for item in raw:

                if not isinstance(
                    item,
                    dict
                ):

                    continue

                country = str(
                    item.get(
                        "country",
                        ""
                    )
                ).upper().strip()

                impact = str(
                    item.get(
                        "impact",
                        ""
                    )
                ).upper().strip()

                if (
                    country != "USD"
                    or
                    impact != "HIGH"
                ):

                    continue

                timestamp = (
                    parse_news_datetime(
                        item.get(
                            "date"
                        )
                    )
                )

                if timestamp is not None:

                    events.append(
                        {
                            "ts":
                                timestamp,

                            "title":
                                str(
                                    item.get(
                                        "title",
                                        "HIGH USD"
                                    )
                                ).strip()
                                or
                                "HIGH USD"
                        }
                    )

        events.sort(
            key=lambda x:
                x["ts"]
        )

        state[
            "news_events"
        ] = events

        state[
            "news_last_success"
        ] = now

        state[
            "news_error_notified"
        ] = False

        print(
            f"NEWS CALENDAR OK: "
            f"{len(events)} "
            f"HIGH USD events",
            flush=True
        )

    except Exception as exc:

        print(
            f"NEWS WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        if not state.get(
            "news_error_notified"
        ):

            telegram(
                "RIObot GOLD NEWS WARNING\n\n"
                "Calendar unavailable.\n"
                "New entries are blocked
