import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD
# XAUUSD | M5 | LIVE PSAR 2nd DOT
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
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10,
        )
    except Exception as exc:
        print(f"TELEGRAM ERROR: {exc}", flush=True)


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
# M5 DATA - VRÁTANE AKTUÁLNEJ OTVORENEJ SVIEČKY
# =========================================================

async def get_m5(account):

    candles = await asyncio.wait_for(
        account.get_historical_candles(
            SYMBOL,
            "5m",
            None,
            120
        ),
        timeout=META_TIMEOUT,
    )

    if not candles or len(candles) < 6:
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

    if len(df) < 6:
        return None

    # Poslednú M5 sviečku NEODSTRAŇUJEME.
    # Použijeme ju na LIVE druhú PSAR bodku.

    df["psar"] = psar_values(df)

    return df


# =========================================================
# LIVE PSAR SIGNAL
# =========================================================

def get_live_signal(df):

    if df is None or len(df) < 4:
        return None

    # A = pred flipom
    # B = prvá bodka
    # C = druhá LIVE bodka

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
# POZÍCIE
# =========================================================

async def get_positions(connection):

    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT,
    )

    return [
        position
        for position in positions
        if position.get(
            "symbol",
            ""
        ).upper() == SYMBOL.upper()
    ]


# =========================================================
# MARKET INFO
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
        ask
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
            digits
        )

        tp = round(
            entry
            + TP_POINTS * point,
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
            timeout=META_TIMEOUT,
        )

    else:

        entry = bid

        sl = round(
            entry
            + SL_POINTS * point,
            digits
        )

        tp = round(
            entry
            - TP_POINTS * point,
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
            "POSITION_TYPE_BUY"
        ):

            profit_points = (
                bid - entry
            ) / point

            new_sl = round(
                entry
                + BE_LOCK * point,
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
                    timeout=META_TIMEOUT,
                )

                print(
                    f"BE BUY -> {new_sl}",
                    flush=True
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
            "POSITION_TYPE_SELL"
        ):

            profit_points = (
                entry - ask
            ) / point

            new_sl = round(
                entry
                - BE_LOCK * point,
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
                    timeout=META_TIMEOUT,
                )

                print(
                    f"BE SELL -> {new_sl}",
                    flush=True
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
            flush=True
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
            flush=True
        )

        if not state[
            "ever_connected"
        ]:

            telegram(
                "RIObot GOLD START / CONNECTED\n\n"
                f"Symbol: {SYMBOL}\n"
                f"Lot: {LOT_SIZE}\n"
                "Timeframe: M5\n"
                "Strategy: LIVE PSAR 2nd DOT\n"
                "Entry: no wait for 2nd candle close\n"
                "TP: +8.00 price move\n"
                "SL: -10.00 price move\n"
                "BE: +5.00 -> +3.00\n"
                "BTCUSD: OFF"
            )

            state[
                "ever_connected"
            ] = True

        else:

            telegram(
                "RIObot GOLD RECONNECTED\n\n"
                f"{SYMBOL} M5\n"
                "MetaApi connection restored."
            )

        while True:

            # BREAK EVEN
            await manage_be(
                connection
            )

            # LIVE M5
            df = await get_m5(
                account
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

                signal = get_live_signal(
                    df
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

                if (
                    signal_key is not None
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

                    # Maximálne 1 XAUUSD pozícia
                    if not positions:

                        # ochrana proti duplicitnému
                        # otvoreniu toho istého signálu
                        state[
                            "last_signal_key"
                        ] = signal_key

                        await open_trade(
                            connection,
                            signal
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
    }

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
