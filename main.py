import asyncio
import json
import os
import time
from datetime import datetime
from threading import Thread

import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi

# =========================================================
# RIObot GOLD
# XAUUSD | M1 | LIVE PSAR 1st DOT + 10s CONFIRM
# MAX 1 trade | TP +8 | SL -10 | BE +7 -> +5
# NEWS: HIGH USD -15 min / +30 min
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_DISTANCE = 8.00
SL_DISTANCE = 10.00
BE_TRIGGER = 7.00
BE_LOCK = 5.00

SIGNAL_CONFIRM_SECONDS = 10
LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

DEFAULT_META_REGION = "london"
MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m1_cache.json"

NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_BEFORE_MINUTES = 15
NEWS_AFTER_MINUTES = 30
NEWS_FETCH_SECONDS = 30 * 60
NEWS_STALE_SECONDS = 3 * 60 * 60

COMMENT = "RIObot GOLD M1 PSAR 1DOT 10S NEWS"

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
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()

# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):
    if not T_TOKEN or not T_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={"chat_id": T_CHAT, "text": message},
            timeout=10,
        )
    except Exception as exc:
        print(f"TELEGRAM WARNING: {type(exc).__name__}: {exc}", flush=True)

# =========================================================
# M1 CACHE
# =========================================================

def load_cache():
    try:
        if not os.path.exists(CACHE_FILE):
            return []
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        clean = []
        for c in data[-MAX_CACHE_BARS:]:
            if all(k in c for k in ("time", "open", "high", "low", "close")):
                clean.append(c)
        return clean
    except Exception as exc:
        print(f"CACHE LOAD WARNING: {type(exc).__name__}: {exc}", flush=True)
        return []


def save_cache(candles):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(candles[-MAX_CACHE_BARS:], f)
    except Exception as exc:
        print(f"CACHE SAVE WARNING: {type(exc).__name__}: {exc}", flush=True)


def update_cache(candles, candle):
    if candle is None:
        return
    if not candles:
        candles.append(candle)
    elif candles[-1]["time"] == candle["time"]:
        candles[-1] = candle
    else:
        candles.append(candle)
    del candles[:-MAX_CACHE_BARS]
    save_cache(candles)

# =========================================================
# MARKET DATA
# =========================================================

async def get_current_m1_candle(region):
    url = (
        f"https://mt-client-api-v1.{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/symbols/{SYMBOL}/"
        f"current-candles/1m?keepSubscription=true"
    )

    def fetch():
        r = requests.get(
            url,
            headers={"Accept": "application/json", "auth-token": M_TOKEN},
            timeout=20,
        )
        if r.status_code != 200:
            raise RuntimeError(f"M1 candle HTTP {r.status_code}: {r.text[:250]}")
        return r.json()

    try:
        c = await asyncio.to_thread(fetch)

        if not c or not all(
            k in c
            for k in ("time", "open", "high", "low", "close")
        ):
            return None

        return {
            "time": str(c["time"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        }

    except Exception as exc:
        print(
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )
        return None


async def get_positions(connection):
    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT
    )

    return [
        p for p in positions
        if str(p.get("symbol", "")).upper() == SYMBOL
    ]


async def get_market(connection):
    spec = await asyncio.wait_for(
        connection.get_symbol_specification(SYMBOL),
        timeout=META_TIMEOUT
    )

    price = await asyncio.wait_for(
        connection.get_symbol_price(SYMBOL),
        timeout=META_TIMEOUT
    )

    digits = int(spec.get("digits", 2))
    tick_size = float(
        spec.get("tickSize")
        or (10 ** (-digits))
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    return tick_size, digits, bid, ask

# =========================================================
# PSAR
# =========================================================

def psar_values(candles):
    n = len(candles)

    if n < 3:
        return [None] * n

    highs = [
        float(c["high"])
        for c in candles
    ]

    lows = [
        float(c["low"])
        for c in candles
    ]

    out = [None] * n

    bull = True
    af = PSAR_STEP
    ep = highs[0]
    sar = lows[0]

    out[0] = sar

    for i in range(1, n):
        sar = sar + af * (ep - sar)

        if bull:
            sar = min(
                sar,
                lows[i - 1]
            )

            if i >= 2:
                sar = min(
                    sar,
                    lows[i - 2]
                )

            if lows[i] < sar:
                bull = False
                sar = ep
                ep = lows[i]
                af = PSAR_STEP

            elif highs[i] > ep:
                ep = highs[i]
                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        else:
            sar = max(
                sar,
                highs[i - 1]
            )

            if i >= 2:
                sar = max(
                    sar,
                    highs[i - 2]
                )

            if highs[i] > sar:
                bull = True
                sar = ep
                ep = highs[i]
                af = PSAR_STEP

            elif lows[i] < ep:
                ep = lows[i]
                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        out[i] = sar

    return out


def get_live_signal(candles):
    if len(candles) < MIN_PSAR_BARS:
        return None, None

    psar = psar_values(candles)

    if psar[-2] is None or psar[-1] is None:
        return None, None

    prev_close = float(
        candles[-2]["close"]
    )

    curr_close = float(
        candles[-1]["close"]
    )

    prev_psar = float(
        psar[-2]
    )

    curr_psar = float(
        psar[-1]
    )

    # Prvá LIVE bodka pod cenou = BUY
    if (
        prev_psar > prev_close
        and curr_psar < curr_close
    ):
        return "BUY", curr_psar

    # Prvá LIVE bodka nad cenou = SELL
    if (
        prev_psar < prev_close
        and curr_psar > curr_close
    ):
        return "SELL", curr_psar

    return None, curr_psar

# =========================================================
# SL / TP / BE
# =========================================================

def normalize_side(value):
    text = str(
        value or ""
    ).upper()

    if "BUY" in text:
        return "BUY"

    if "SELL" in text:
        return "SELL"

    return None


def exact_levels(
    side,
    entry,
    digits
):
    if side == "BUY":
        return (
            round(
                entry - SL_DISTANCE,
                digits
            ),
            round(
                entry + TP_DISTANCE,
                digits
            ),
        )

    return (
        round(
            entry + SL_DISTANCE,
            digits
        ),
        round(
            entry - TP_DISTANCE,
            digits
        ),
    )


async def wait_for_position(
    connection,
    side,
    attempts=20
):
    for _ in range(attempts):

        for p in await get_positions(
            connection
        ):
            if normalize_side(
                p.get("type")
            ) == side:
                return p

        await asyncio.sleep(
            0.25
        )

    return None


async def ensure_exact_stops(
    connection
):
    positions = await get_positions(
        connection
    )

    if not positions:
        return

    _, digits, _, _ = await get_market(
        connection
    )

    epsilon = (
        10 ** (-digits)
    ) / 2

    for p in positions:
        position_id = p.get(
            "id"
        )

        side = normalize_side(
            p.get("type")
        )

        entry = float(
            p.get(
                "openPrice",
                0
            )
            or 0
        )

        if (
            not position_id
            or side not in ("BUY", "SELL")
            or entry <= 0
        ):
            continue

        exact_sl, exact_tp = exact_levels(
            side,
            entry,
            digits
        )

        sl_raw = p.get(
            "stopLoss"
        )

        tp_raw = p.get(
            "takeProfit"
        )

        current_sl = (
            float(sl_raw)
            if sl_raw not in (None, 0)
            else None
        )

        current_tp = (
            float(tp_raw)
            if tp_raw not in (None, 0)
            else None
        )

        # Ak si ručne dáš lepší SL,
        # RIObot ho NEZHORŠÍ.
        if (
            side == "BUY"
            and current_sl is not None
            and current_sl >= entry
        ):
            target_sl = current_sl

        elif (
            side == "SELL"
            and current_sl is not None
            and current_sl <= entry
        ):
            target_sl = current_sl

        else:
            target_sl = exact_sl

        needs_sl = (
            current_sl is None
            or abs(
                current_sl - target_sl
            ) > epsilon
        )

        needs_tp = (
            current_tp is None
            or abs(
                current_tp - exact_tp
            ) > epsilon
        )

        if needs_sl or needs_tp:

            await asyncio.wait_for(
                connection.modify_position(
                    position_id,
                    target_sl,
                    exact_tp
                ),
                timeout=META_TIMEOUT
            )

            print(
                f"LEVELS {side} "
                f"ENTRY={entry:.2f} "
                f"SL={target_sl:.2f} "
                f"TP={exact_tp:.2f}",
                flush=True
            )


async def manage_be(
    connection
):
    positions = await get_positions(
        connection
    )

    if not positions:
        return

    _, digits, bid, ask = await get_market(
        connection
    )

    for p in positions:
        position_id = p.get(
            "id"
        )

        side = normalize_side(
            p.get("type")
        )

        entry = float(
            p.get(
                "openPrice",
                0
            )
            or 0
        )

        if (
            not position_id
            or side not in ("BUY", "SELL")
            or entry <= 0
        ):
            continue

        sl_raw = p.get(
            "stopLoss"
        )

        tp_raw = p.get(
            "takeProfit"
        )

        current_sl = (
            float(sl_raw)
            if sl_raw not in (None, 0)
            else None
        )

        current_tp = (
            float(tp_raw)
            if tp_raw not in (None, 0)
            else None
        )

        if side == "BUY":

            profit_distance = (
                bid - entry
            )

            new_sl = round(
                entry + BE_LOCK,
                digits
            )

            should_move = (
                profit_distance >= BE_TRIGGER
                and (
                    current_sl is None
                    or current_sl < new_sl
                )
            )

        else:

            profit_distance = (
                entry - ask
            )

            new_sl = round(
                entry - BE_LOCK,
                digits
            )

            should_move = (
                profit_distance >= BE_TRIGGER
                and (
                    current_sl is None
                    or current_sl > new_sl
                )
            )

        if should_move:

            await asyncio.wait_for(
                connection.modify_position(
                    position_id,
                    new_sl,
                    current_tp
                ),
                timeout=META_TIMEOUT
            )

            print(
                f"BE {side} -> "
                f"{new_sl:.2f}",
                flush=True
            )

            telegram(
                "RIObot GOLD BE\n\n"
                f"{side} {SYMBOL}\n"
                "+7.00 reached\n"
                "SL locked +5.00\n"
                f"SL: {new_sl:.2f}"
            )

# =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(
    connection,
    side
):
    _, digits, bid, ask = await get_market(
        connection
    )

    provisional_entry = (
        ask
        if side == "BUY"
        else bid
    )

    provisional_sl, provisional_tp = exact_levels(
        side,
        provisional_entry,
        digits
    )

    if side == "BUY":

        await asyncio.wait_for(
            connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                provisional_sl,
                provisional_tp,
                {
                    "comment":
                        COMMENT
                },
            ),
            timeout=META_TIMEOUT
        )

    else:

        await asyncio.wait_for(
            connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                provisional_sl,
                provisional_tp,
                {
                    "comment":
                        COMMENT
                },
            ),
            timeout=META_TIMEOUT
        )

    position = await wait_for_position(
        connection,
        side
    )

    if position is None:

        telegram(
            "RIObot GOLD\n\n"
            f"{side} {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            "Order opened; exact SL/TP "
            "will sync next cycle.\n"
            "Timeframe: M1\n"
            "PSAR: LIVE 1st DOT "
            "+ 10s confirm\n"
            "BE: +7.00 -> +5.00\n"
            "NEWS: HIGH USD "
            "-15m / +30m"
        )

        return

    position_id = position.get(
        "id"
    )

    actual_entry = float(
        position.get(
            "openPrice",
            0
        )
        or 0
    )

    exact_sl, exact_tp = exact_levels(
        side,
        actual_entry,
        digits
    )

    await asyncio.wait_for(
        connection.modify_position(
            position_id,
            exact_sl,
            exact_tp
        ),
        timeout=META_TIMEOUT
    )

    print(
        f"ORDER OK {side} {SYMBOL} "
        f"ENTRY={actual_entry:.2f} "
        f"SL={exact_sl:.2f} "
        f"TP={exact_tp:.2f}",
        flush=True
    )

    telegram(
        "RIObot GOLD\n\n"
        f"{side} {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Entry: {actual_entry:.2f}\n"
        f"SL: {exact_sl:.2f}\n"
        f"TP: {exact_tp:.2f}\n"
        "Timeframe: M1\n"
        "PSAR: LIVE 1st DOT "
        "+ 10s confirm\n"
        "BE: +7.00 -> +5.00\n"
        "NEWS: HIGH USD "
        "-15m / +30m"
    )

# =========================================================
# NEWS FILTER
# =========================================================

def parse_news_time(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        ).timestamp()

    except Exception:
        return None


async def refresh_news(
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
        r = requests.get(
            NEWS_URL,
            headers={
                "Accept":
                    "application/json",
                "User-Agent":
                    "RIObot-GOLD/1.0"
            },
            timeout=20
        )

        r.raise_for_status()

        return r.json()

    try:
        raw = await asyncio.to_thread(
            fetch
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

                ts = parse_news_time(
                    item.get("date")
                )

                if ts is None:
                    continue

                events.append(
                    {
                        "ts":
                            ts,
                        "title":
                            str(
                                item.get(
                                    "title",
                                    "HIGH USD"
                                )
                            ).strip()
                            or "HIGH USD",
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
            "HIGH USD events",
            flush=True
        )

    except Exception as exc:

        print(
            f"NEWS CALENDAR WARNING: "
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

        ts = float(
            event.get(
                "ts",
                0.0
            )
            or 0.0
        )

        if (
            ts - before
            <= now
            <= ts + after
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
        account = await asyncio.wait_for(
            api
            .metatrader_account_api
            .get_account(
                M_ACC
            ),
            timeout=META_TIMEOUT
        )

        region = str(
            getattr(
                account,
                "region",
                None
            )
            or DEFAULT_META_REGION
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

        await refresh_news(
            state,
            force=True
        )

        loop_errors = 0

        while True:

            try:
                # Otvorený obchod sa vždy
                # spravuje aj počas NEWS.
                await ensure_exact_stops(
                    connection
                )

                await manage_be(
                    connection
                )

                await refresh_news(
                    state
                )

                candle = await get_current_m1_candle(
                    state[
                        "meta_region"
                    ]
                )

                if candle is not None:

                    update_cache(
                        state[
                            "m1_candles"
                        ],
                        candle
                    )

                candles = state[
                    "m1_candles"
                ]

                if (
                    len(candles)
                    < MIN_PSAR_BARS
                ):

                    if (
                        len(candles)
                        != state[
                            "last_warmup_count"
                        ]
                    ):

                        print(
                            f"M1 WARMUP "
                            f"{len(candles)}/"
                            f"{MIN_PSAR_BARS}",
                            flush=True
                        )

                        state[
                            "last_warmup_count"
                        ] = len(candles)

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                signal, live_psar = get_live_signal(
                    candles
                )

                candle_time = str(
                    candles[-1][
                        "time"
                    ]
                )

                signal_key = (
                    f"{candle_time}|"
                    f"{signal}"
                    if signal
                    else None
                )

                positions = await get_positions(
                    connection
                )

                # Ak je obchod otvorený,
                # neotvorí ďalší.
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

                    if signal_key:

                        state[
                            "last_signal_key"
                        ] = signal_key

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # Ak PSAR bodka zmizla
                # počas 10 s, signál sa ruší.
                if signal_key is None:

                    if (
                        state.get(
                            "pending_signal_key"
                        )
                        is not None
                    ):

                        print(
                            "10S CONFIRM CANCELLED: "
                            "PSAR signal disappeared",
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

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # Rovnaký signál už
                # druhýkrát nepoužije.
                if (
                    signal_key
                    == state.get(
                        "last_signal_key"
                    )
                ):

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                (
                    news_blocked,
                    news_reason,
                    news_event
                ) = news_block_status(
                    state
                )

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

                    event_key = (
                        f"{int(news_event['ts'])}|"
                        f"{news_event['title']}"
                        if news_event
                        else news_reason
                    )

                    if (
                        state.get(
                            "news_block_notified"
                        )
                        != event_key
                    ):

                        if news_event:

                            event_time = datetime.fromtimestamp(
                                news_event[
                                    "ts"
                                ]
                            ).astimezone().strftime(
                                "%H:%M"
                            )

                            telegram(
                                "RIObot GOLD "
                                "NEWS BLOCK\n\n"
                                f"HIGH USD: "
                                f"{news_event['title']}\n"
                                f"Time: "
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
                        f"NEWS BLOCK: "
                        f"{news_reason}",
                        flush=True
                    )

                    loop_errors = 0

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                state[
                    "news_block_notified"
                ] = None

                now_mono = time.monotonic()

                # Prvá bodka:
                # iba spustí 10-sekundové čakanie.
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
                        f"1ST DOT PENDING 10S "
                        f"{signal} "
                        f"M1={candle_time} "
                        f"PSAR={live_psar}",
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

                    if (
                        elapsed
                        >= SIGNAL_CONFIRM_SECONDS
                        and signal
                        == state.get(
                            "pending_signal_side"
                        )
                    ):

                        # Tesne pred otvorením
                        # ešte raz skontroluje
                        # NEWS a pozície.
                        (
                            news_blocked2,
                            _,
                            _
                        ) = news_block_status(
                            state
                        )

                        positions2 = await get_positions(
                            connection
                        )

                        if (
                            not news_blocked2
                            and not positions2
                        ):

                            print(
                                f"1ST DOT CONFIRMED 10S "
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
                    f"LOOP WARNING "
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
                    f"CLOSE WARNING: "
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
            None,
    }

    print(
        f"M1 CACHE LOADED: "
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
                f"BOT SESSION ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}",
                flush=True
            )

            print(
                f"RECONNECT IN "
                f"{RECONNECT_SECONDS} "
                "SECONDS...",
                flush=True
            )

            telegram(
                "RIObot GOLD "
                "CONNECTION ERROR\n\n"
                f"Reconnect in "
                f"{RECONNECT_SECONDS} "
                "seconds."
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


if __name__ == "__main__":
    asyncio.run(
        main()
                )
