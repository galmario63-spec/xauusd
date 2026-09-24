import asyncio
import json
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD
# XAUUSD | M1 | AGGRESSIVE LIVE PSAR 1st DOT
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

# XAUUSD pri tickSize 0.01
TP_POINTS = 800.0       # +8.00
SL_POINTS = 1000.0      # -10.00

# BREAK EVEN
BE_TRIGGER = 600.0      # pri +6.00
BE_LOCK = 500.0         # zamkne +5.00

COMMENT = "RIObot GOLD M1 PSAR 1DOT"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30
MAX_LOOP_ERRORS = 3

DEFAULT_META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m1_cache.json"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# RENDER WEB SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD M1 ACTIVE", 200


def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


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
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
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
# M1 CACHE
# =========================================================

def load_cache():
    try:
        if not os.path.exists(CACHE_FILE):
            return []

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if not isinstance(data, list):
            return []

        clean = []

        for candle in data[-MAX_CACHE_BARS:]:
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
                clean.append(candle)

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
                candles[-MAX_CACHE_BARS:],
                file
            )

    except Exception as exc:
        print(
            f"CACHE SAVE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )


def update_candle_cache(candles, candle):
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
# CURRENT LIVE M1 CANDLE
# =========================================================

async def get_current_m1_candle(region):
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
                "Accept": "application/json",
                "auth-token": M_TOKEN,
            },
            timeout=20,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"M1 candle HTTP "
                f"{response.status_code}: "
                f"{response.text[:250]}"
            )

        return response.json()

    try:
        candle = await asyncio.to_thread(fetch)

        if not candle:
            return None

        required = (
            "time",
            "open",
            "high",
            "low",
            "close"
        )

        if not all(key in candle for key in required):
            print(
                f"M1 CANDLE WARNING: missing fields: {candle}",
                flush=True
            )
            return None

        return {
            "time": str(candle["time"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
        }

    except Exception as exc:
        print(
            f"M1 CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True
        )
        return None


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(df):
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()

    count = len(df)

    if count < 3:
        return [None] * count

    psar = [None] * count

    bull = True
    af = PSAR_STEP
    ep = highs[0]
    sar = lows[0]

    psar[0] = sar

    for i in range(1, count):
        sar = sar + af * (ep - sar)

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
                    af + PSAR_STEP,
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
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        psar[i] = sar

    return psar


# =========================================================
# DATAFRAME
# =========================================================

def make_m1_dataframe(candles):
    if len(candles) < MIN_PSAR_BARS:
        return None

    df = pd.DataFrame(candles)

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

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    ).reset_index(drop=True)

    if len(df) < MIN_PSAR_BARS:
        return None

    df["psar"] = psar_values(df)

    return df


# =========================================================
# AGGRESSIVE LIVE SIGNAL
# PRVÁ PSAR BODKA = VSTUP
# =========================================================

def get_live_signal(df):
    if df is None or len(df) < 3:
        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

    previous_close = float(previous["close"])
    current_close = float(current["close"])

    previous_psar = float(previous["psar"])
    current_psar = float(current["psar"])

    # BUY - prvá LIVE PSAR bodka POD cenou
    if (
        previous_psar > previous_close
        and current_psar < current_close
    ):
        return "BUY"

    # SELL - prvá LIVE PSAR bodka NAD cenou
    if (
        previous_psar < previous_close
        and current_psar > current_close
    ):
        return "SELL"

    return None


# =========================================================
# POSITIONS
# =========================================================

async def get_positions(connection):
    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT
    )

    return [
        position
        for position in positions
        if str(
            position.get(
                "symbol",
                ""
            )
        ).upper() == SYMBOL.upper()
    ]


# =========================================================
# MARKET
# =========================================================

async def get_market(connection):
    specification = await asyncio.wait_for(
        connection.get_symbol_specification(SYMBOL),
        timeout=META_TIMEOUT
    )

    price = await asyncio.wait_for(
        connection.get_symbol_price(SYMBOL),
        timeout=META_TIMEOUT
    )

    digits = int(
        specification.get(
            "digits",
            2
        )
    )

    point = specification.get("tickSize")

    if not point:
        point = 10 ** (-digits)

    point = float(point)

    bid = float(price["bid"])
    ask = float(price["ask"])

    return point, digits, bid, ask


# =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(connection, side):
    point, digits, bid, ask = await get_market(connection)

    if side == "BUY":
        entry = ask

        sl = round(
            entry - SL_POINTS * point,
            digits
        )

        tp = round(
            entry + TP_POINTS * point,
            digits
        )

        result = await asyncio.wait_for(
            connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            ),
            timeout=META_TIMEOUT
        )

    else:
        entry = bid

        sl = round(
            entry + SL_POINTS * point,
            digits
        )

        tp = round(
            entry - TP_POINTS * point,
            digits
        )

        result = await asyncio.wait_for(
            connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            ),
            timeout=META_TIMEOUT
        )

    print(
        f"ORDER OK "
        f"{side} {SYMBOL} "
        f"ENTRY={entry} "
        f"SL={sl} "
        f"TP={tp}",
        flush=True
    )

    telegram(
        "RIObot GOLD\n\n"
        f"{side} {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        "Timeframe: M1\n"
        "PSAR: LIVE 1st DOT\n"
        "BE: +6.00 -> +5.00"
    )

    return result


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):
    positions = await get_positions(connection)

    if not positions:
        return

    point, digits, bid, ask = await get_market(connection)

    for position in positions:
        position_id = position.get("id")

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
        )

        current_sl = position.get("stopLoss")
        current_tp = position.get("takeProfit")

        if current_sl is not None:
            current_sl = float(current_sl)

        if current_tp is not None:
            current_tp = float(current_tp)

        # BUY: pri +6 presunie SL na +5
        if side in (
            "BUY",
            "POSITION_TYPE_BUY"
        ):
            profit_points = (
                bid - entry
            ) / point

            new_sl = round(
                entry + BE_LOCK * point,
                digits
            )

            if (
                profit_points >= BE_TRIGGER
                and (
                    current_sl is None
                    or current_sl < new_sl
                )
            ):
                await asyncio.wait_for(
                    connection.modify_position(
                        position_id,
                        new_sl,
                        current_tp
                    ),
                    timeout=META_TIMEOUT
                )

                print(
                    f"BE BUY -> {new_sl}",
                    flush=True
                )

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"BUY {SYMBOL}\n"
                    "+6.00 reached\n"
                    "SL locked +5.00\n"
                    f"SL: {new_sl}"
                )

        # SELL: pri +6 presunie SL na +5
        elif side in (
            "SELL",
            "POSITION_TYPE_SELL"
        ):
            profit_points = (
                entry - ask
            ) / point

            new_sl = round(
                entry - BE_LOCK * point,
                digits
            )

            if (
                profit_points >= BE_TRIGGER
                and (
                    current_sl is None
                    or current_sl > new_sl
                )
            ):
                await asyncio.wait_for(
                    connection.modify_position(
                        position_id,
                        new_sl,
                        current_tp
                    ),
                    timeout=META_TIMEOUT
                )

                print(
                    f"BE SELL -> {new_sl}",
                    flush=True
                )

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"SELL {SYMBOL}\n"
                    "+6.00 reached\n"
                    "SL locked +5.00\n"
                    f"SL: {new_sl}"
                )


# =========================================================
# METAAPI SESSION
# =========================================================

async def bot_session(state):
    api = MetaApi(M_TOKEN)
    connection = None

    try:
        account = await asyncio.wait_for(
            api.metatrader_account_api.get_account(M_ACC),
            timeout=META_TIMEOUT
        )

        region = getattr(
            account,
            "region",
            None
        )

        if not region:
            region = DEFAULT_META_REGION

        region = str(region).lower()
        state["meta_region"] = region

        print(
            f"METAAPI REGION: {region}",
            flush=True
        )

        print(
            "CONNECTING METAAPI...",
            flush=True
        )

        connection = account.get_rpc_connection()

        await asyncio.wait_for(
            connection.connect(),
            timeout=60
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=120
        )

        print(
            "RIObot GOLD CONNECTED",
            flush=True
        )

        if not state["ever_connected"]:
            telegram(
                "RIObot GOLD START / CONNECTED\n\n"
                f"Symbol: {SYMBOL}\n"
                f"Lot: {LOT_SIZE}\n"
                "Timeframe: M1\n"
                "Strategy: AGGRESSIVE LIVE PSAR 1st DOT\n"
                "MAX: 1 XAUUSD position\n"
                "TP: +8.00\n"
                "SL: -10.00\n"
                "BE: +6.00 -> +5.00"
            )

            state["ever_connected"] = True

        else:
            telegram(
                "RIObot GOLD RECONNECTED\n\n"
                f"{SYMBOL} M1\n"
                "MetaApi connection restored."
            )

        loop_errors = 0

        while True:
            try:
                await manage_be(connection)

                candle = await get_current_m1_candle(
                    state["meta_region"]
                )

                if candle is not None:
                    update_candle_cache(
                        state["m1_candles"],
                        candle
                    )

                    count = len(
                        state["m1_candles"]
                    )

                    if count < MIN_PSAR_BARS:
                        if (
                            count
                            != state["last_warmup_count"]
                        ):
                            print(
                                f"M1 WARMUP "
                                f"{count}/"
                                f"{MIN_PSAR_BARS}",
                                flush=True
                            )

                            state["last_warmup_count"] = count

                    else:
                        df = make_m1_dataframe(
                            state["m1_candles"]
                        )

                        if df is not None:
                            current_candle = df.iloc[-1]

                            candle_time = str(
                                current_candle.get("time")
                            )

                            signal = get_live_signal(df)

                            signal_key = None

                            if signal in (
                                "BUY",
                                "SELL"
                            ):
                                signal_key = (
                                    f"{candle_time}|{signal}"
                                )

                            if (
                                signal_key is not None
                                and signal_key
                                != state["last_signal_key"]
                            ):
                                print(
                                    "1ST DOT SIGNAL "
                                    f"{signal} "
                                    f"M1={candle_time} "
                                    f"PSAR="
                                    f"{current_candle['psar']}",
                                    flush=True
                                )

                                positions = await get_positions(
                                    connection
                                )

                                if not positions:
                                    state["last_signal_key"] = signal_key

                                    await open_trade(
                                        connection,
                                        signal
                                    )

                                else:
                                    state["last_signal_key"] = signal_key

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

                if loop_errors >= MAX_LOOP_ERRORS:
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
                    f"{type(exc).__name__}: {exc}",
                    flush=True
                )


# =========================================================
# MAIN / AUTO RECONNECT
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
        "ever_connected": False,
        "last_signal_key": None,
        "m1_candles": load_cache(),
        "last_warmup_count": -1,
        "meta_region": DEFAULT_META_REGION,
    }

    print(
        "M1 CACHE LOADED: "
        f"{len(state['m1_candles'])} bars",
        flush=True
    )

    while True:
        try:
            await bot_session(state)

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
                f"SECONDS...",
                flush=True
            )

            telegram(
                "RIObot GOLD CONNECTION ERROR\n\n"
                f"Reconnect in "
                f"{RECONNECT_SECONDS} seconds."
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
