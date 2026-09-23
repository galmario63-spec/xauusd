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
# XAUUSD | M5 | LIVE PSAR 2nd DOT | MT5 cloud-g2
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_POINTS = 800.0
SL_POINTS = 1000.0

BE_TRIGGER = 500.0
BE_LOCK = 300.0

COMMENT = "RIObot GOLD M5 LIVE PSAR 2DOT"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30

META_REGION = "london"

MIN_PSAR_BARS = 6
MAX_CACHE_BARS = 120
CACHE_FILE = "m5_cache.json"


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
        print(f"TELEGRAM ERROR: {exc}", flush=True)


# =========================================================
# M5 CACHE
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
            if all(
                k in c
                for k in ("time", "open", "high", "low", "close")
            ):
                clean.append(c)

        return clean

    except Exception as exc:
        print(f"CACHE LOAD WARNING: {exc}", flush=True)
        return []


def save_cache(candles):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                candles[-MAX_CACHE_BARS:],
                f
            )
    except Exception as exc:
        print(f"CACHE SAVE WARNING: {exc}", flush=True)


# =========================================================
# CURRENT LIVE M5 CANDLE
# =========================================================

async def get_current_m5_candle():

    url = (
        f"https://mt-client-api-v1.{META_REGION}."
        f"agiliumtrade.ai/users/current/accounts/"
        f"{M_ACC}/symbols/{SYMBOL}/"
        f"current-candles/5m?keepSubscription=true"
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
                f"M5 candle HTTP "
                f"{response.status_code}: "
                f"{response.text[:300]}"
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
            "close",
        )

        if not all(k in candle for k in required):
            print(
                f"M5 CANDLE WARNING: "
                f"missing fields: {candle}",
                flush=True,
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
        # chyba sviečky už NESPUSTÍ reconnect slučku
        print(
            f"M5 CANDLE WARNING: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


def update_candle_cache(candles, candle):

    if candle is None:
        return candles

    if not candles:
        candles.append(candle)

    elif candles[-1]["time"] == candle["time"]:
        # stále tá istá LIVE M5 sviečka
        candles[-1] = candle

    else:
        # nová M5 sviečka
        candles.append(candle)

    candles[:] = candles[-MAX_CACHE_BARS:]

    save_cache(candles)

    return candles


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
                    lows[i - 2],
                )
            else:
                sar = min(
                    sar,
                    lows[i - 1],
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
                    PSAR_MAX,
                )

        else:

            if i >= 2:
                sar = max(
                    sar,
                    highs[i - 1],
                    highs[i - 2],
                )
            else:
                sar = max(
                    sar,
                    highs[i - 1],
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
                    PSAR_MAX,
                )

        psar[i] = sar

    return psar


def make_m5_dataframe(candles):

    if len(candles) < MIN_PSAR_BARS:
        return None

    df = pd.DataFrame(candles)

    for col in (
        "open",
        "high",
        "low",
        "close",
    ):
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    ).reset_index(drop=True)

    if len(df) < MIN_PSAR_BARS:
        return None

    df["psar"] = psar_values(df)

    return df


# =========================================================
# LIVE PSAR SIGNAL
# =========================================================

def get_live_signal(df):

    if df is None or len(df) < 4:
        return None

    # A = pred flipom
    # B = prvá PSAR bodka
    # C = druhá LIVE PSAR bodka

    a = df.iloc[-3]
    b = df.iloc[-2]
    c = df.iloc[-1]

    a_close = float(a["close"])
    b_close = float(b["close"])
    c_close = float(c["close"])

    a_psar = float(a["psar"])
    b_psar = float(b["psar"])
    c_psar = float(c["psar"])

    # BUY
    if (
        a_psar > a_close
        and b_psar < b_close
        and c_psar < c_close
    ):
        return "BUY"

    # SELL
    if (
        a_psar < a_close
        and b_psar > b_close
        and c_psar > c_close
    ):
        return "SELL"

    return None


# =========================================================
# POSITIONS
# =========================================================

async def get_positions(connection):

    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT,
    )

    return [
        p
        for p in positions
        if p.get(
            "symbol",
            ""
        ).upper() == SYMBOL.upper()
    ]


# =========================================================
# MARKET
# =========================================================

async def get_market(connection):

    specification = await asyncio.wait_for(
        connection.get_symbol_specification(
            SYMBOL
        ),
        timeout=META_TIMEOUT,
    )

    price = await asyncio.wait_for(
        connection.get_symbol_price(
            SYMBOL
        ),
        timeout=META_TIMEOUT,
    )

    digits = int(
        specification.get(
            "digits",
            2
        )
    )

    point = specification.get(
        "tickSize"
    )

    if not point:
        point = 10 ** (-digits)

    point = float(point)

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
        ask,
    )


# =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(
    connection,
    side
):

    point, digits, bid, ask = (
        await get_market(connection)
    )

    if side == "BUY":

        entry = ask

        sl = round(
            entry
            - SL_POINTS * point,
            digits,
        )

        tp = round(
            entry
            + TP_POINTS * point,
            digits,
        )

        result = await asyncio.wait_for(
            connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                },
            ),
            timeout=META_TIMEOUT,
        )

    else:

        entry = bid

        sl = round(
            entry
            + SL_POINTS * point,
            digits,
        )

        tp = round(
            entry
            - TP_POINTS * point,
            digits,
        )

        result = await asyncio.wait_for(
            connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                },
            ),
            timeout=META_TIMEOUT,
        )

    print(
        f"ORDER OK {side} {SYMBOL} "
        f"ENTRY={entry} "
        f"SL={sl} "
        f"TP={tp}",
        flush=True,
    )

    telegram(
        "RIObot GOLD\n\n"
        f"{side} {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        "PSAR: LIVE 2nd dot\n"
        "BE: +5.00 -> +3.00"
    )

    return result


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):

    positions = await get_positions(
        connection
    )

    if not positions:
        return

    point, digits, bid, ask = (
        await get_market(connection)
    )

    for position in positions:

        position_id = position.get(
            "id"
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
        )

        current_sl = position.get(
            "stopLoss"
        )

        current_tp = position.get(
            "takeProfit"
        )

        if current_sl is not None:
            current_sl = float(
                current_sl
            )

        if current_tp is not None:
            current_tp = float(
                current_tp
            )

        # BUY
        if side in (
            "BUY",
            "POSITION_TYPE_BUY",
        ):

            profit_points = (
                bid - entry
            ) / point

            new_sl = round(
                entry
                + BE_LOCK * point,
                digits,
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
                        current_tp,
                    ),
                    timeout=META_TIMEOUT,
                )

                print(
                    f"BE BUY -> {new_sl}",
                    flush=True,
                )

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"BUY {SYMBOL}\n"
                    "+5.00 reached\n"
                    "SL locked +3.00\n"
                    f"SL: {new_sl}"
                )

        # SELL
        elif side in (
            "SELL",
            "POSITION_TYPE_SELL",
        ):

            profit_points = (
                entry - ask
            ) / point

            new_sl = round(
                entry
                - BE_LOCK * point,
                digits,
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
                        current_tp,
                    ),
                    timeout=META_TIMEOUT,
                )

                print(
                    f"BE SELL -> {new_sl}",
                    flush=True,
                )

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"SELL {SYMBOL}\n"
                    "+5.00 reached\n"
                    "SL locked +3.00\n"
                    f"SL: {new_sl}"
                )


# =========================================================
# METAAPI SESSION
# =========================================================

async def bot_session(state):

    api = MetaApi(
        M_TOKEN
    )

    connection = None

    try:

        account = await asyncio.wait_for(
            api.metatrader_account_api.get_account(
                M_ACC
            ),
            timeout=META_TIMEOUT,
        )

        print(
            "CONNECTING METAAPI...",
            flush=True,
        )

        connection = (
            account.get_rpc_connection()
        )

        await asyncio.wait_for(
            connection.connect(),
            timeout=60,
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=120,
        )

        print(
            "RIObot GOLD CONNECTED",
            flush=True,
        )

        if not state["ever_connected"]:

            telegram(
                "RIObot GOLD START / CONNECTED\n\n"
                f"Symbol: {SYMBOL}\n"
                f"Lot: {LOT_SIZE}\n"
                "Timeframe: M5\n"
                "Strategy: LIVE PSAR 2nd DOT\n"
                "MT5 cloud-g2\n"
                "TP: +8.00\n"
                "SL: -10.00\n"
                "BE: +5.00 -> +3.00"
            )

            state["ever_connected"] = True

        else:

            telegram(
                "RIObot GOLD RECONNECTED\n\n"
                f"{SYMBOL} M5\n"
                "MetaApi connection restored."
            )

        while True:

            # BE má prioritu
            await manage_be(
                connection
            )

            # LIVE aktuálna M5
            candle = (
                await get_current_m5_candle()
            )

            if candle is not None:

                update_candle_cache(
                    state["m5_candles"],
                    candle,
                )

                count = len(
                    state["m5_candles"]
                )

                if count < MIN_PSAR_BARS:

                    if (
                        count
                        != state[
                            "last_warmup_count"
                        ]
                    ):

                        print(
                            f"M5 WARMUP "
                            f"{count}/"
                            f"{MIN_PSAR_BARS}",
                            flush=True,
                        )

                        state[
                            "last_warmup_count"
                        ] = count

                else:

                    df = make_m5_dataframe(
                        state["m5_candles"]
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
                            "SELL",
                        ):

                            signal_key = (
                                f"{candle_time}|"
                                f"{signal}"
                            )

                        if (
                            signal_key
                            is not None
                            and signal_key
                            != state[
                                "last_signal_key"
                            ]
                        ):

                            print(
                                f"LIVE SIGNAL "
                                f"{signal} "
                                f"M5={candle_time} "
                                f"PSAR="
                                f"{current_candle['psar']}",
                                flush=True,
                            )

                            positions = (
                                await get_positions(
                                    connection
                                )
                            )

                            # MAX 1 XAUUSD
                            if not positions:

                                state[
                                    "last_signal_key"
                                ] = signal_key

                                await open_trade(
                                    connection,
                                    signal,
                                )

                            else:

                                state[
                                    "last_signal_key"
                                ] = signal_key

            await asyncio.sleep(
                LOOP_SECONDS
            )

    finally:

        if connection is not None:

            try:

                await asyncio.wait_for(
                    connection.close(),
                    timeout=10,
                )

            except Exception as exc:

                print(
                    f"CLOSE WARNING: "
                    f"{exc}",
                    flush=True,
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
        "ever_connected": False,
        "last_signal_key": None,
        "m5_candles": load_cache(),
        "last_warmup_count": -1,
    }

    print(
        f"M5 CACHE LOADED: "
        f"{len(state['m5_candles'])} bars",
        flush=True,
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
                flush=True,
            )

            print(
                f"RECONNECT IN "
                f"{RECONNECT_SECONDS} "
                f"SECONDS...",
                flush=True,
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
