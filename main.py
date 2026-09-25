import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD
# XAUUSD | M1 | LIVE PSAR 1st DOT
# MAX 1 trade
# TP +8.00 | SL -10.00 | BE +7.00 -> +5.00
# NEWS: HIGH USD, 15 min before / 30 min after = no new trade
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 8.00
SL_DISTANCE = 10.00

BE_TRIGGER = 7.00
BE_LOCK = 5.00

COMMENT = "RIObot GOLD M1 PSAR 1DOT NEWS"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m1_cache.json"


# =========================================================
# NEWS FILTER
# =========================================================

NEWS_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

NEWS_COUNTRY = "USD"
NEWS_IMPACT = "High"

NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30

NEWS_REFRESH_SECONDS = 900
NEWS_RETRY_SECONDS = 60

# Ak sa kalendar 2 hodiny nepodari obnovit,
# robot pre istotu neotvori novy obchod.
NEWS_MAX_STALE_SECONDS = 7200

NEWS_REQUEST_TIMEOUT = 15


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# RENDER KEEP ALIVE
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD ACTIVE", 200


def run_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
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
            timeout=10
        )

    except Exception as exc:

        print(
            f"TELEGRAM WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )


# =========================================================
# NEWS TIME
# =========================================================

def parse_news_time(value):

    try:

        text = str(
            value or ""
        ).strip()

        if not text:
            return None

        dt = datetime.fromisoformat(
            text.replace(
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

    except Exception:

        return None


# =========================================================
# DOWNLOAD NEWS
# =========================================================

def fetch_news_sync():

    response = requests.get(
        NEWS_URL,
        headers={
            "Accept":
                "application/json",

            "User-Agent":
                "RIObot-GOLD/1.0"
        },
        timeout=NEWS_REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(
        data,
        list
    ):

        raise RuntimeError(
            "Invalid NEWS calendar JSON"
        )

    events = []

    for item in data:

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
        ).upper()

        impact = str(
            item.get(
                "impact",
                ""
            )
        ).lower()

        # Len USD HIGH impact
        if country != NEWS_COUNTRY:
            continue

        if (
            impact
            != NEWS_IMPACT.lower()
        ):

            continue

        event_time = (
            parse_news_time(
                item.get(
                    "date"
                )
            )
        )

        if event_time is None:
            continue

        events.append(
            {
                "title":
                    str(
                        item.get(
                            "title",
                            "USD HIGH NEWS"
                        )
                    ),

                "time":
                    event_time
            }
        )

    events.sort(
        key=lambda x:
            x["time"]
    )

    return events


# =========================================================
# REFRESH NEWS
# =========================================================

async def refresh_news(
    state,
    force=False
):

    now_ts = (
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    if (
        not force
        and
        now_ts
        < state[
            "news_next_fetch"
        ]
    ):

        return

    try:

        events = (
            await asyncio.to_thread(
                fetch_news_sync
            )
        )

        state[
            "news_events"
        ] = events

        state[
            "news_last_success"
        ] = now_ts

        state[
            "news_next_fetch"
        ] = (
            now_ts
            + NEWS_REFRESH_SECONDS
        )

        state[
            "news_error_notified"
        ] = False

        print(
            "NEWS CALENDAR OK: "
            f"{len(events)} "
            "HIGH USD events",
            flush=True
        )

    except Exception as exc:

        state[
            "news_next_fetch"
        ] = (
            now_ts
            + NEWS_RETRY_SECONDS
        )

        print(
            "NEWS CALENDAR WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        if not state[
            "news_error_notified"
        ]:

            telegram(
                "RIObot NEWS FILTER WARNING\n\n"
                "Economic calendar unavailable.\n"
                "If calendar becomes too old, "
                "new trades pause.\n"
                "Open trades still use "
                "SL / TP / BE."
            )

            state[
                "news_error_notified"
            ] = True


# =========================================================
# CHECK NEWS BLOCK
# =========================================================

def get_news_block(
    state
):

    now = datetime.now(
        timezone.utc
    )

    now_ts = (
        now.timestamp()
    )

    last_success = (
        state.get(
            "news_last_success",
            0.0
        )
    )

    # Ak nemame spolahlivy kalendar,
    # radsej neotvorime novy obchod.
    if (
        last_success <= 0
        or
        now_ts
        - last_success
        > NEWS_MAX_STALE_SECONDS
    ):

        return {
            "kind":
                "unavailable",

            "title":
                "NEWS CALENDAR UNAVAILABLE",

            "time":
                None
        }

    before = timedelta(
        minutes=
        NEWS_BEFORE_MINUTES
    )

    after = timedelta(
        minutes=
        NEWS_AFTER_MINUTES
    )

    for event in state.get(
        "news_events",
        []
    ):

        event_time = (
            event["time"]
        )

        if (
            event_time
            - before
            <= now
            <= event_time
            + after
        ):

            return {
                "kind":
                    "event",

                "title":
                    event["title"],

                "time":
                    event_time
            }

    return None


# =========================================================
# M1 CACHE
# =========================================================

def load_cache():

    try:

        if not os.path.exists(
            CACHE_FILE
        ):

            return []

        with open(
            CACHE_FILE,
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

        clean = []

        for candle in data[
            -MAX_CACHE_BARS:
        ]:

            if all(
                key in candle
                for key in (
                    "time",
                    "open",
                    "high",
                    "low",
                    "close"
                )
            ):

                clean.append(
                    candle
                )

        return clean

    except Exception as exc:

        print(
            "CACHE LOAD WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        return []


def save_cache(
    candles
):

    try:

        with open(
            CACHE_FILE,
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
            "CACHE SAVE WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )


def update_candle_cache(
    candles,
    candle
):

    if candle is None:
        return

    if (
        candles
        and
        candles[-1]["time"]
        == candle["time"]
    ):

        candles[-1] = candle

    else:

        candles.append(
            candle
        )

    del candles[
        :-MAX_CACHE_BARS
    ]

    save_cache(
        candles
    )


# =========================================================
# LIVE M1 CANDLE
# =========================================================

async def get_current_m1_candle(
    region
):

    url = (
        f"https://mt-client-api-v1."
        f"{region}."
        f"agiliumtrade.ai/"
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
                    M_TOKEN
            },
            timeout=20
        )

        if (
            response.status_code
            != 200
        ):

            raise RuntimeError(
                "M1 candle HTTP "
                f"{response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:

        candle = (
            await asyncio.to_thread(
                fetch
            )
        )

        if not candle:
            return None

        required = (
            "time",
            "open",
            "high",
            "low",
            "close"
        )

        if not all(
            key in candle
            for key in required
        ):

            print(
                "M1 CANDLE WARNING: "
                "missing fields: "
                f"{candle}",
                flush=True
            )

            return None

        return {
            "time":
                str(
                    candle["time"]
                ),

            "open":
                float(
                    candle["open"]
                ),

            "high":
                float(
                    candle["high"]
                ),

            "low":
                float(
                    candle["low"]
                ),

            "close":
                float(
                    candle["close"]
                )
        }

    except Exception as exc:

        print(
            "M1 CANDLE WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        return None


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(
    df
):

    highs = (
        df["high"]
        .astype(float)
        .tolist()
    )

    lows = (
        df["low"]
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

    ep = highs[0]
    sar = lows[0]

    psar[0] = sar

    for i in range(
        1,
        count
    ):

        sar = (
            sar
            + af
            * (
                ep
                - sar
            )
        )

        if bull:

            sar = min(
                sar,
                lows[i - 1],
                (
                    lows[i - 2]
                    if i >= 2
                    else lows[i - 1]
                )
            )

            if lows[i] < sar:

                bull = False

                sar = ep
                ep = lows[i]
                af = PSAR_STEP

            elif highs[i] > ep:

                ep = highs[i]

                af = min(
                    af
                    + PSAR_STEP,
                    PSAR_MAX
                )

        else:

            sar = max(
                sar,
                highs[i - 1],
                (
                    highs[i - 2]
                    if i >= 2
                    else highs[i - 1]
                )
            )

            if highs[i] > sar:

                bull = True

                sar = ep
                ep = highs[i]
                af = PSAR_STEP

            elif lows[i] < ep:

                ep = lows[i]

                af = min(
                    af
                    + PSAR_STEP,
                    PSAR_MAX
                )

        psar[i] = sar

    return psar


# =========================================================
# DATAFRAME
# =========================================================

def make_m1_dataframe(
    candles
):

    if (
        len(candles)
        < MIN_PSAR_BARS
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

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = (
        df.dropna(
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
        len(df)
        < MIN_PSAR_BARS
    ):

        return None

    df["psar"] = (
        psar_values(
            df
        )
    )

    return df


# =========================================================
# LIVE PSAR 1st DOT
# =========================================================

def get_live_signal(
    df
):

    if (
        df is None
        or
        len(df) < 3
    ):

        return None

    previous = (
        df.iloc[-2]
    )

    current = (
        df.iloc[-1]
    )

    previous_close = float(
        previous["close"]
    )

    current_close = float(
        current["close"]
    )

    previous_psar = float(
        previous["psar"]
    )

    current_psar = float(
        current["psar"]
    )

    # BUY:
    # predtym PSAR NAD cenou
    # teraz 1. LIVE bodka POD cenou

    if (
        previous_psar
        > previous_close
        and
        current_psar
        < current_close
    ):

        return "BUY"

    # SELL:
    # predtym PSAR POD cenou
    # teraz 1. LIVE bodka NAD cenou

    if (
        previous_psar
        < previous_close
        and
        current_psar
        > current_close
    ):

        return "SELL"

    return None


# =========================================================
# POSITIONS
# =========================================================

async def get_positions(
    connection
):

    positions = (
        await asyncio.wait_for(
            connection
            .get_positions(),

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

        == SYMBOL.upper()
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

    tick_size = (
        specification.get(
            "tickSize"
        )
    )

    if not tick_size:

        tick_size = (
            10 ** (
                -digits
            )
        )

    return (
        float(
            tick_size
        ),

        digits,

        float(
            price["bid"]
        ),

        float(
            price["ask"]
        )
    )


# =========================================================
# NORMALIZE SIDE
# =========================================================

def normalize_side(
    value
):

    side = str(
        value or ""
    ).upper()

    if side in (
        "BUY",
        "POSITION_TYPE_BUY"
    ):

        return "BUY"

    if side in (
        "SELL",
        "POSITION_TYPE_SELL"
    ):

        return "SELL"

    return side


# =========================================================
# EXACT TP / SL
# =========================================================

def exact_levels(
    side,
    entry,
    digits
):

    side = normalize_side(
        side
    )

    if side == "BUY":

        sl = round(
            entry
            - SL_DISTANCE,
            digits
        )

        tp = round(
            entry
            + TP_DISTANCE,
            digits
        )

    else:

        sl = round(
            entry
            + SL_DISTANCE,
            digits
        )

        tp = round(
            entry
            - TP_DISTANCE,
            digits
        )

    return (
        sl,
        tp
    )


# =========================================================
# WAIT FOR REAL MT5 POSITION
# =========================================================

async def wait_for_symbol_position(
    connection,
    side,
    attempts=24
):

    wanted = normalize_side(
        side
    )

    for _ in range(
        attempts
    ):

        positions = (
            await get_positions(
                connection
            )
        )

        for position in positions:

            position_side = (
                normalize_side(
                    position.get(
                        "type"
                    )
                )
            )

            if (
                position_side
                == wanted
            ):

                return position

        await asyncio.sleep(
            0.25
        )

    return None


# =========================================================
# ENSURE EXACT SL / TP
# OD REALNEJ openPrice
# =========================================================

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
    ) = await get_market(
        connection
    )

    epsilon = (
        10 ** (
            -digits
        )
    ) / 2

    for position in positions:

        position_id = (
            position.get(
                "id"
            )
        )

        side = normalize_side(
            position.get(
                "type"
            )
        )

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or 0
        )

        if (
            not position_id
            or
            entry <= 0
            or
            side not in (
                "BUY",
                "SELL"
            )
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
            else None
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
            else None
        )

        # Ak uz BE zamkol zisk,
        # SL nikdy nevratime naspat.

        if (
            side == "BUY"
            and
            current_sl
            is not None
            and
            current_sl >= entry
        ):

            target_sl = (
                current_sl
            )

        elif (
            side == "SELL"
            and
            current_sl
            is not None
            and
            current_sl <= entry
        ):

            target_sl = (
                current_sl
            )

        else:

            target_sl = (
                exact_sl
            )

        needs_sl = (
            current_sl is None
            or
            abs(
                current_sl
                - target_sl
            )
            > epsilon
        )

        needs_tp = (
            current_tp is None
            or
            abs(
                current_tp
                - exact_tp
            )
            > epsilon
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

            print(
                "EXACT LEVELS "
                f"{side} "
                f"ENTRY={entry} "
                f"SL={target_sl} "
                f"TP={exact_tp}",
                flush=True
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
    ) = await get_market(
        connection
    )

    # Najprv docasny ochranny SL/TP.
    # Po otvoreni ich opravime
    # presne od realnej openPrice.

    provisional_entry = (
        ask
        if side == "BUY"
        else bid
    )

    (
        provisional_sl,
        provisional_tp
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
                    provisional_sl,
                    provisional_tp,

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
                    provisional_sl,
                    provisional_tp,

                    {
                        "comment":
                            COMMENT
                    }
                ),

                timeout=META_TIMEOUT
            )
        )

    # Nacitame realnu MT5 openPrice.

    position = (
        await wait_for_symbol_position(
            connection,
            side
        )
    )

    if position is None:

        print(
            "ORDER OK "
            f"{side} "
            f"{SYMBOL}; "
            "waiting for MT5 "
            "position sync",
            flush=True
        )

        return result

    position_id = (
        position.get(
            "id"
        )
    )

    actual_entry = float(
        position.get(
            "openPrice",
            0
        )
        or 0
    )

    if (
        not position_id
        or
        actual_entry <= 0
    ):

        return result

    (
        exact_sl,
        exact_tp
    ) = exact_levels(
        side,
        actual_entry,
        digits
    )

    await asyncio.wait_for(

        connection
        .modify_position(
            position_id,
            exact_sl,
            exact_tp
        ),

        timeout=META_TIMEOUT
    )

    print(
        "ORDER OK "
        f"{side} "
        f"{SYMBOL} "
        "REAL ENTRY="
        f"{actual_entry} "
        f"SL={exact_sl} "
        f"TP={exact_tp}",
        flush=True
    )

    telegram(
        "RIObot GOLD\n\n"

        f"{side} "
        f"{SYMBOL}\n"

        f"Lot: "
        f"{LOT_SIZE}\n"

        f"Entry: "
        f"{actual_entry:.2f}\n"

        f"SL: "
        f"{exact_sl:.2f}\n"

        f"TP: "
        f"{exact_tp:.2f}\n"

        "Timeframe: M1\n"

        "PSAR: LIVE 1st DOT\n"

        "BE: +7.00 -> +5.00\n"

        "NEWS: HIGH USD "
        "-15m / +30m"
    )

    return result


# =========================================================
# BREAK EVEN
# +7.00 -> LOCK +5.00
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
    ) = await get_market(
        connection
    )

    for position in positions:

        position_id = (
            position.get(
                "id"
            )
        )

        side = normalize_side(
            position.get(
                "type"
            )
        )

        entry = float(
            position.get(
                "openPrice",
                0
            )
            or 0
        )

        if (
            not position_id
            or
            entry <= 0
            or
            side not in (
                "BUY",
                "SELL"
            )
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
            else None
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
            else None
        )

        # BUY

        if side == "BUY":

            profit_distance = (
                bid
                - entry
            )

            new_sl = round(
                entry
                + BE_LOCK,
                digits
            )

            should_move = (
                profit_distance
                >= BE_TRIGGER
                and
                (
                    current_sl
                    is None
                    or
                    current_sl
                    < new_sl
                )
            )

        # SELL

        else:

            profit_distance = (
                entry
                - ask
            )

            new_sl = round(
                entry
                - BE_LOCK,
                digits
            )

            should_move = (
                profit_distance
                >= BE_TRIGGER
                and
                (
                    current_sl
                    is None
                    or
                    current_sl
                    > new_sl
                )
            )

        if should_move:

            await asyncio.wait_for(

                connection
                .modify_position(
                    position_id,
                    new_sl,
                    current_tp
                ),

                timeout=META_TIMEOUT
            )

            print(
                "BE "
                f"{side} "
                "-> "
                f"{new_sl}",
                flush=True
            )

            telegram(
                "RIObot GOLD BE\n\n"

                f"{side} "
                f"{SYMBOL}\n"

                "+7.00 reached\n"

                "SL locked +5.00\n"

                f"SL: "
                f"{new_sl:.2f}"
            )


# =========================================================
# METAAPI SESSION
# =========================================================

async def bot_session(
    state
):

    api = MetaApi(
        M_TOKEN
    )

    connection = None

    try:

        account = (
            await asyncio.wait_for(

                api
                .metatrader_account_api
                .get_account(
                    M_ACC
                ),

                timeout=META_TIMEOUT
            )
        )

        region = str(
            getattr(
                account,
                "region",
                None
            )
            or
            DEFAULT_META_REGION
        ).lower()

        state[
            "meta_region"
        ] = region

        print(
            "METAAPI REGION: "
            f"{region}",
            flush=True
        )

        print(
            "CONNECTING METAAPI...",
            flush=True
        )

        connection = (
            account
            .get_rpc_connection()
        )

        await asyncio.wait_for(
            connection.connect(),
            timeout=60
        )

        await asyncio.wait_for(
            connection
            .wait_synchronized(),
            timeout=120
        )

        print(
            "RIObot GOLD CONNECTED",
            flush=True
        )

        # Nacitame NEWS kalendar
        # hned po pripojeni.

        await refresh_news(
            state,
            force=True
        )

        if not state[
            "ever_connected"
        ]:

            telegram(
                "RIObot GOLD "
                "START / CONNECTED\n\n"

                f"Symbol: "
                f"{SYMBOL}\n"

                f"Lot: "
                f"{LOT_SIZE}\n"

                "Timeframe: M1\n"

                "Strategy: "
                "LIVE PSAR 1st DOT\n"

                "MAX: 1 XAUUSD "
                "position\n"

                "TP: +8.00\n"

                "SL: -10.00\n"

                "BE: +7.00 -> +5.00\n"

                "NEWS: HIGH USD "
                "-15m / +30m"
            )

            state[
                "ever_connected"
            ] = True

        else:

            telegram(
                "RIObot GOLD "
                "RECONNECTED\n\n"

                f"{SYMBOL} M1\n"

                "MetaApi connection "
                "restored."
            )

        loop_errors = 0

        while True:

            try:

                # =================================
                # OTVORENY OBCHOD SA RIADI VZDY
                # AJ POCAS NEWS
                # =================================

                await ensure_exact_stops(
                    connection
                )

                await manage_be(
                    connection
                )

                await refresh_news(
                    state
                )

                # =================================
                # LIVE M1
                # =================================

                candle = (
                    await get_current_m1_candle(

                        state[
                            "meta_region"
                        ]
                    )
                )

                if candle is not None:

                    update_candle_cache(

                        state[
                            "m1_candles"
                        ],

                        candle
                    )

                    count = len(

                        state[
                            "m1_candles"
                        ]
                    )

                    # =====================
                    # WARMUP
                    # =====================

                    if (
                        count
                        < MIN_PSAR_BARS
                    ):

                        if (
                            count
                            != state[
                                "last_warmup_count"
                            ]
                        ):

                            print(
                                "M1 WARMUP "
                                f"{count}/"
                                f"{MIN_PSAR_BARS}",
                                flush=True
                            )

                            state[
                                "last_warmup_count"
                            ] = count

                    else:

                        df = (
                            make_m1_dataframe(

                                state[
                                    "m1_candles"
                                ]
                            )
                        )

                        if df is not None:

                            current = (
                                df.iloc[-1]
                            )

                            candle_time = str(
                                current.get(
                                    "time"
                                )
                            )

                            signal = (
                                get_live_signal(
                                    df
                                )
                            )

                            signal_key = None

                            if signal in (
                                "BUY",
                                "SELL"
                            ):

                                signal_key = (
                                    f"{candle_time}|"
                                    f"{signal}"
                                )

                            # =====================
                            # NOVY PSAR SIGNAL
                            # =====================

                            if (
                                signal_key
                                is not None
                                and
                                signal_key
                                != state[
                                    "last_signal_key"
                                ]
                            ):

                                print(
                                    "1ST DOT SIGNAL "
                                    f"{signal} "
                                    "M1="
                                    f"{candle_time} "
                                    "PSAR="
                                    f"{current['psar']}",
                                    flush=True
                                )

                                positions = (
                                    await get_positions(
                                        connection
                                    )
                                )

                                # ochrana pred
                                # duplicitnym signalom

                                state[
                                    "last_signal_key"
                                ] = signal_key

                                # =================
                                # MAX 1 OBCHOD
                                # =================

                                if not positions:

                                    block = (
                                        get_news_block(
                                            state
                                        )
                                    )

                                    # =================
                                    # BEZ NEWS = OBCHOD
                                    # =================

                                    if block is None:

                                        state[
                                            "news_block_notified"
                                        ] = None

                                        await open_trade(
                                            connection,
                                            signal
                                        )

                                    # =================
                                    # HIGH USD NEWS
                                    # =================

                                    elif (
                                        block["kind"]
                                        == "event"
                                    ):

                                        event_key = (
                                            f"{block['time'].isoformat()}|"
                                            f"{block['title']}"
                                        )

                                        print(
                                            "NEWS BLOCK: "
                                            f"{block['title']} | "
                                            "UTC="
                                            f"{block['time'].isoformat()}",
                                            flush=True
                                        )

                                        if (
                                            state[
                                                "news_block_notified"
                                            ]
                                            != event_key
                                        ):

                                            telegram(
                                                "RIObot NEWS BLOCK\n\n"

                                                "USD HIGH: "
                                                f"{block['title']}\n"

                                                "No new trade: "
                                                "15 min before / "
                                                "30 min after.\n"

                                                "Open trades keep "
                                                "SL / TP / BE."
                                            )

                                            state[
                                                "news_block_notified"
                                            ] = event_key

                                    # =================
                                    # CALENDAR ERROR
                                    # =================

                                    else:

                                        print(
                                            "NEWS BLOCK: "
                                            "calendar unavailable/stale",
                                            flush=True
                                        )

                loop_errors = 0

            except Exception as exc:

                loop_errors += 1

                print(
                    "LOOP WARNING "
                    f"{loop_errors}/"
                    f"{MAX_LOOP_ERRORS}: "
                    f"{type(exc).__name__}: "
                    f"{exc}",
                    flush=True
                )

                if (
                    loop_errors
                    >= MAX_LOOP_ERRORS
                ):

                    raise

                await asyncio.sleep(
                    RECONNECT_SECONDS
                )

            await asyncio.sleep(
                LOOP_SECONDS
            )

    finally:

        if connection is not None:

            try:

                await asyncio.wait_for(
                    connection.close(),
                    timeout=10
                )

            except Exception as exc:

                print(
                    "CLOSE WARNING: "
                    f"{type(exc).__name__}: "
                    f"{exc}",
                    flush=True
                )


# =========================================================
# MAIN
# =========================================================

async def main():

    keep_alive()

    if not M_TOKEN:

        raise RuntimeError(
            "M_TOKEN is missing"
        )

    if not M_ACC:

        raise RuntimeError(
            "M_ACC is missing"
        )

    state = {

        "ever_connected":
            False,

        "last_signal_key":
            None,

        "m1_candles":
            load_cache(),

        "last_warmup_count":
            -1,

        "meta_region":
            DEFAULT_META_REGION,

        "news_events":
            [],

        "news_last_success":
            0.0,

        "news_next_fetch":
            0.0,

        "news_error_notified":
            False,

        "news_block_notified":
            None
    }

    print(
        "M1 CACHE LOADED: "
        f"{len(state['m1_candles'])} "
        "bars",
        flush=True
    )

    while True:

        try:

            await bot_session(
                state
            )

        except Exception as exc:

            print(
                "BOT SESSION ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}",
                flush=True
            )

            print(
                "RECONNECT IN "
                f"{RECONNECT_SECONDS} "
                "SECONDS...",
                flush=True
            )

            telegram(
                "RIObot GOLD "
                "CONNECTION ERROR\n\n"

                "Reconnect in "
                f"{RECONNECT_SECONDS} "
                "seconds."
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
