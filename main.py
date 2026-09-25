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


SYMBOL = "XAUUSD"
LOT_SIZE = 1.0

PSAR_STEP = 0.02
PSAR_MAX = 0.2

TP_DISTANCE = 8.0
SL_DISTANCE = 10.0

BE_TRIGGER_DISTANCE = 7.0
BE_LOCK_DISTANCE = 5.0

COMMENT = "RIObot GOLD M1 PSAR 1DOT 10S NEWS"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

# Prvá PSAR bodka musí zostať platná 10 sekúnd
SIGNAL_CONFIRM_SECONDS = 10

# HIGH USD NEWS
NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30
NEWS_FETCH_SECONDS = 30 * 60
NEWS_STALE_SECONDS = 3 * 60 * 60

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m1_cache.json"


M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


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


def telegram(message):

    if not T_TOKEN or not T_CHAT:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )

    except Exception as exc:
        print(
            f"TELEGRAM WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


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
            f"CACHE LOAD WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )

        return []


def save_cache(candles):

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
            f"CACHE SAVE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


def update_candle_cache(
    candles,
    candle
):

    if candle is None:
        return

    if not candles:
        candles.append(
            candle
        )

    elif (
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
                f"M1 candle HTTP "
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
                f"missing fields: "
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
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        return None


def psar_values(df):

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

    count = len(df)

    if count < 3:
        return [None] * count

    psar = (
        [None] * count
    )

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

            if i >= 2:
                sar = min(
                    sar,
                    lows[i - 1],
                    lows[i - 2]
                )

            else:
                sar = min(
                    sar,
                    lows[i - 1]
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

            if i >= 2:
                sar = max(
                    sar,
                    highs[i - 1],
                    highs[i - 2]
                )

            else:
                sar = max(
                    sar,
                    highs[i - 1]
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

        df[col] = (
            pd.to_numeric(
                df[col],
                errors="coerce"
            )
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


def get_live_signal(df):

    if (
        df is None
        or len(df) < 3
    ):
        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

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
    # PSAR bol nad cenou
    # a nová LIVE bodka je pod cenou
    if (
        previous_psar
        > previous_close
        and
        current_psar
        < current_close
    ):
        return "BUY"

    # SELL:
    # PSAR bol pod cenou
    # a nová LIVE bodka je nad cenou
    if (
        previous_psar
        < previous_close
        and
        current_psar
        > current_close
    ):
        return "SELL"

    return None


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
        for position in positions
        if str(
            position.get(
                "symbol",
                ""
            )
        ).upper()
        == SYMBOL.upper()
    ]


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

    point = (
        specification.get(
            "tickSize"
        )
    )

    if not point:
        point = (
            10
            ** (-digits)
        )

    point = float(
        point
    )

    bid = float(
        price["bid"]
    )

    ask = float(
        price["ask"]
    )

    return (
        point,
        digits,
        bid,
        ask
    )


def exact_levels(
    side,
    entry,
    digits
):

    if side in (
        "BUY",
        "POSITION_TYPE_BUY"
    ):

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


async def wait_for_symbol_position(
    connection,
    side,
    attempts=20
):

    buy_types = (
        "BUY",
        "POSITION_TYPE_BUY"
    )

    sell_types = (
        "SELL",
        "POSITION_TYPE_SELL"
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

            pside = str(
                position.get(
                    "type",
                    ""
                )
            ).upper()

            if (
                side == "BUY"
                and pside
                in buy_types
            ):
                return position

            if (
                side == "SELL"
                and pside
                in sell_types
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
    ) = await get_market(
        connection
    )

    epsilon = (
        10
        ** (-digits)
        / 2
    )

    for position in positions:

        position_id = (
            position.get("id")
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
            or 0
        )

        if (
            not position_id
            or entry <= 0
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
            if current_sl_raw
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
            if current_tp_raw
            not in (
                None,
                0
            )
            else None
        )

        # Ak si ručne posunieš BUY SL
        # na BE alebo do zisku,
        # robot ho nevráti späť.
        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):

            target_sl = (
                current_sl
                if (
                    current_sl
                    is not None
                    and current_sl
                    >= entry
                )
                else exact_sl
            )

        # Rovnako pri SELL.
        elif side in (
            "SELL",
            "POSITION_TYPE_SELL"
        ):

            target_sl = (
                current_sl
                if (
                    current_sl
                    is not None
                    and current_sl
                    <= entry
                )
                else exact_sl
            )

        else:
            continue

        needs_sl = (
            current_sl is None
            or abs(
                current_sl
                - target_sl
            )
            > epsilon
        )

        needs_tp = (
            current_tp is None
            or abs(
                current_tp
                - exact_tp
            )
            > epsilon
        )

        if (
            needs_sl
            or needs_tp
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
                f"EXACT LEVELS "
                f"{side} "
                f"ENTRY={entry} "
                f"SL={target_sl} "
                f"TP={exact_tp}",
                flush=True
            )


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

    position = (
        await wait_for_symbol_position(
            connection,
            side
        )
    )

    if position is not None:

        position_id = (
            position.get("id")
        )

        actual_entry = float(
            position.get(
                "openPrice",
                0
            )
            or 0
        )

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
            f"ORDER OK "
            f"{side} "
            f"{SYMBOL} "
            f"REAL ENTRY="
            f"{actual_entry} "
            f"SL={exact_sl} "
            f"TP={exact_tp}",
            flush=True
        )

        telegram(
            "RIObot GOLD\n\n"
            f"{side} {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            f"Entry: "
            f"{actual_entry:.2f}\n"
            f"SL: {exact_sl:.2f}\n"
            f"TP: {exact_tp:.2f}\n"
            "Timeframe: M1\n"
            "PSAR: LIVE 1st DOT "
            "+ 10s confirm\n"
            "BE: +7.00 -> +5.00\n"
            "NEWS: HIGH USD "
            "-15m / +30m"
        )

    else:

        print(
            f"ORDER OK "
            f"{side} "
            f"{SYMBOL}; "
            "waiting for exact "
            "openPrice sync",
            flush=True
        )

        telegram(
            "RIObot GOLD\n\n"
            f"{side} {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            "Order opened. "
            "Exact SL/TP sync "
            "on next cycle.\n"
            "Timeframe: M1\n"
            "PSAR: LIVE 1st DOT "
            "+ 10s confirm\n"
            "BE: +7.00 -> +5.00\n"
            "NEWS: HIGH USD "
            "-15m / +30m"
        )

    return result


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
            position.get("id")
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
            or 0
        )

        if (
            not position_id
            or entry <= 0
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
            if current_sl_raw
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
            if current_tp_raw
            not in (
                None,
                0
            )
            else None
        )

        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):

            profit_distance = (
                bid
                - entry
            )

            new_sl = round(
                entry
                + BE_LOCK_DISTANCE,
                digits
            )

            should_move = (
                profit_distance
                >= BE_TRIGGER_DISTANCE
                and
                (
                    current_sl
                    is None
                    or current_sl
                    < new_sl
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
                    f"BE BUY -> "
                    f"{new_sl}",
                    flush=True
                )

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"BUY {SYMBOL}\n"
                    "+7.00 reached\n"
                    "SL locked +5.00\n"
                    f"SL: {new_sl:.2f}"
                )

        elif side in (
            "SELL",
            "POSITION_TYPE_SELL"
        ):

            profit_distance = (
                entry
                - ask
            )

            new_sl = round(
                entry
                - BE_LOCK_DISTANCE,
                digits
            )

            should_move = (
                profit_distance
                >= BE_TRIGGER_DISTANCE
                and
                (
                    current_sl
                    is None
                    or current_sl
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
                    f"BE SELL -> "
                    f"{new_sl}",
                    flush=True
                )

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"SELL {SYMBOL}\n"
                    "+7.00 reached\n"
                    "SL locked +5.00\n"
                    f"SL: {new_sl:.2f}"
                )


def parse_news_datetime(
    value
):

    if not value:
        return None

    text = str(
        value
    ).strip()

    try:
        return (
            datetime
            .fromisoformat(
                text.replace(
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
        and now
        < state.get(
            "news_next_fetch",
            0.0
        )
    ):
        return

    state[
        "news_next_fetch"
    ] = (
        now
        + NEWS_FETCH_SECONDS
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
            timeout=20
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
                    or impact != "HIGH"
                ):
                    continue

                ts = (
                    parse_news_datetime(
                        item.get("date")
                    )
                )

                if ts is None:
                    continue

                title = str(
                    item.get(
                        "title",
                        "HIGH USD"
                    )
                ).strip()

                if not title:
                    title = "HIGH USD"

                events.append(
                    {
                        "ts": ts,
                        "title": title
                    }
                )

        events.sort(
            key=lambda event:
                event["ts"]
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
            "NEWS CALENDAR OK: "
            f"{len(events)} "
            "HIGH USD events",
            flush=True
        )

    except Exception as exc:

        print(
            "NEWS CALENDAR WARNING: "
            f"{type(exc).__name__}: "
            f"{exc}",
            flush=True
        )

        if not state.get(
            "news_error_notified",
            False
        ):

            telegram(
                "RIObot GOLD "
                "NEWS WARNING\n\n"
                "Economic calendar "
                "unavailable.\n"
                "New entries will be "
                "blocked if calendar "
                "becomes stale.\n"
                "Open trades keep "
                "SL / TP / BE."
            )

            state[
                "news_error_notified"
            ] = True


def news_block_status(
    state
):

    now = time.time()

    last_ok = float(
        state.get(
            "news_last_success",
            0.0
        )
        or 0.0
    )

    if (
        last_ok <= 0
        or now - last_ok
        > NEWS_STALE_SECONDS
    ):
        return (
            True,
            "calendar unavailable/stale",
            None
        )

    before = (
        NEWS_BEFORE_MINUTES
        * 60
    )

    after = (
        NEWS_AFTER_MINUTES
        * 60
    )

    for event in state.get(
        "news_events",
        []
    ):

        event_ts = float(
            event.get(
                "ts",
                0.0
            )
            or 0.0
        )

        if (
            event_ts - before
            <= now
            <= event_ts + after
        ):
            return (
                True,
                "HIGH USD",
                event
            )

    return (
        False,
        None,
        None
    )


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

        region = getattr(
            account,
            "region",
            None
        )

        if not region:
            region = (
                DEFAULT_META_REGION
            )

        region = str(
            region
        ).lower()

        state[
            "meta_region"
        ] = region

        print(
            f"METAAPI REGION: "
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

        if not state[
            "ever_connected"
        ]:

            telegram(
                "RIObot GOLD "
                "START / CONNECTED\n\n"

                f"Symbol: {SYMBOL}\n"
                f"Lot: {LOT_SIZE}\n"

                "Timeframe: M1\n"

                "Strategy: LIVE PSAR "
                "1st DOT + 10s confirm\n"

                "MT5 cloud-g2\n"

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

        await refresh_news_calendar(
            state,
            force=True
        )

        loop_errors = 0

        while True:

            try:

                # Existujúci obchod ostáva
                # plne spravovaný.
                await ensure_exact_stops(
                    connection
                )

                await manage_be(
                    connection
                )

                await refresh_news_calendar(
                    state
                )

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

                            current_candle = (
                                df.iloc[-1]
                            )

                            candle_time = str(
                                current_candle.get(
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

                            positions = (
                                await get_positions(
                                    connection
                                )
                            )

                            # =========================
                            # UŽ JE OTVORENÝ OBCHOD
                            # =========================

                            if positions:

                                state[
                                    "pending_signal_key"
                                ] = None

                                state[
                                    "pending_signal_side"
                                ] = None

                                state[
                                    "pending_signal_started"
                                ] = 0.0

                                # Rovnaký M1 signál už po
                                # zavretí obchodu nepoužije.
                                if (
                                    signal_key
                                    is not None
                                ):

                                    state[
                                        "last_signal_key"
                                    ] = signal_key

                            # =========================
                            # PSAR SIGNÁL ZMIZOL
                            # =========================

                            elif (
                                signal_key
                                is None
                            ):

                                if (
                                    state.get(
                                        "pending_signal_key"
                                    )
                                    is not None
                                ):

                                    print(
                                        "10S CONFIRM "
                                        "CANCELLED: "
                                        "PSAR signal "
                                        "disappeared",
                                        flush=True
                                    )

                                state[
                                    "pending_signal_key"
                                ] = None

                                state[
                                    "pending_signal_side"
                                ] = None

                                state[
                                    "pending_signal_started"
                                ] = 0.0

                            # =========================
                            # TENTO SIGNÁL UŽ BOL POUŽITÝ
                            # =========================

                            elif (
                                signal_key
                                == state.get(
                                    "last_signal_key"
                                )
                            ):

                                pass

                            # =========================
                            # NOVÝ PSAR SIGNÁL
                            # =========================

                            else:

                                (
                                    news_blocked,
                                    news_reason,
                                    news_event
                                ) = (
                                    news_block_status(
                                        state
                                    )
                                )

                                # =====================
                                # NEWS BLOK
                                # =====================

                                if news_blocked:

                                    state[
                                        "pending_signal_key"
                                    ] = None

                                    state[
                                        "pending_signal_side"
                                    ] = None

                                    state[
                                        "pending_signal_started"
                                    ] = 0.0

                                    if (
                                        news_event
                                        is not None
                                    ):

                                        event_key = (
                                            f"{int(news_event['ts'])}|"
                                            f"{news_event['title']}"
                                        )

                                    else:

                                        event_key = (
                                            news_reason
                                        )

                                    if (
                                        state.get(
                                            "news_block_notified"
                                        )
                                        != event_key
                                    ):

                                        if (
                                            news_event
                                            is not None
                                        ):

                                            event_time = (
                                                datetime
                                                .fromtimestamp(
                                                    news_event[
                                                        "ts"
                                                    ]
                                                )
                                                .astimezone()
                                                .strftime(
                                                    "%H:%M"
                                                )
                                            )

                                            telegram(
                                                "RIObot GOLD "
                                                "NEWS BLOCK\n\n"

                                                "HIGH USD: "
                                                f"{news_event['title']}\n"

                                                "Time: "
                                                f"{event_time}\n"

                                                "No new trade "
                                                "-15m / +30m.\n"

                                                "Open trades keep "
                                                "SL / TP / BE."
                                            )

                                        else:

                                            telegram(
                                                "RIObot GOLD "
                                                "NEWS BLOCK\n\n"

                                                "Calendar "
                                                "unavailable/stale.\n"

                                                "No new trades "
                                                "until calendar "
                                                "is valid.\n"

                                                "Open trades keep "
                                                "SL / TP / BE."
                                            )

                                        state[
                                            "news_block_notified"
                                        ] = event_key

                                    print(
                                        "NEWS BLOCK: "
                                        f"{news_reason}",
                                        flush=True
                                    )

                                # =====================
                                # SIGNÁL JE POVOLENÝ
                                # =====================

                                else:

                                    state[
                                        "news_block_notified"
                                    ] = None

                                    now_mono = (
                                        time.monotonic()
                                    )

                                    # Prvýkrát sa objavila
                                    # 1. PSAR bodka.
                                    # Ešte NEOTVORÍ obchod.
                                    if (
                                        state.get(
                                            "pending_signal_key"
                                        )
                                        != signal_key
                                    ):

                                        state[
                                            "pending_signal_key"
                                        ] = signal_key

                                        state[
                                            "pending_signal_side"
                                        ] = signal

                                        state[
                                            "pending_signal_started"
                                        ] = now_mono

                                        print(
                                            "1ST DOT "
                                            "PENDING 10S "
                                            f"{signal} "
                                            f"M1={candle_time} "
                                            "PSAR="
                                            f"{current_candle['psar']}",
                                            flush=True
                                        )

                                    else:

                                        elapsed = (
                                            now_mono
                                            - float(
                                                state.get(
                                                    "pending_signal_started",
                                                    0.0
                                                )
                                                or 0.0
                                            )
                                        )

                                        # Po 10 sekundách musí
                                        # rovnaká PSAR bodka
                                        # stále existovať.
                                        if (
                                            elapsed
                                            >= SIGNAL_CONFIRM_SECONDS
                                            and signal
                                            == state.get(
                                                "pending_signal_side"
                                            )
                                        ):

                                            # News ešte raz tesne
                                            # pred orderom.
                                            (
                                                news_blocked2,
                                                _,
                                                _
                                            ) = (
                                                news_block_status(
                                                    state
                                                )
                                            )

                                            # Ešte raz overíme,
                                            # že nie je otvorený
                                            # žiadny obchod.
                                            positions2 = (
                                                await get_positions(
                                                    connection
                                                )
                                            )

                                            if (
                                                not news_blocked2
                                                and not positions2
                                            ):

                                                print(
                                                    "1ST DOT "
                                                    "CONFIRMED 10S "
                                                    f"{signal} "
                                                    f"M1={candle_time}",
                                                    flush=True
                                                )

                                                state[
                                                    "last_signal_key"
                                                ] = signal_key

                                                state[
                                                    "pending_signal_key"
                                                ] = None

                                                state[
                                                    "pending_signal_side"
                                                ] = None

                                                state[
                                                    "pending_signal_started"
                                                ] = 0.0

                                                await open_trade(
                                                    connection,
                                                    signal
                                                )

                                            else:

                                                state[
                                                    "pending_signal_key"
                                                ] = None

                                                state[
                                                    "pending_signal_side"
                                                ] = None

                                                state[
                                                    "pending_signal_started"
                                                ] = 0.0

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

        "pending_signal_key":
            None,

        "pending_signal_side":
            None,

        "pending_signal_started":
            0.0,

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


if __name__ == "__main__":

    asyncio.run(
        main()
                    )                                        "15 min before / "
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
