import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# NASTAVENIA
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 0.50

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_POINTS = 750.0
SL_POINTS = 500.0

BE_TRIGGER = 300.0
BE_LOCK = 100.0

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15

# Po kolkych po sebe iducich connection chybach
# spravime uplny reconnect
MAX_CONNECTION_ERRORS = 2

COMMENT = "RIObot GOLD M5 PSAR BE"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# RENDER SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD M5 ACTIVE"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
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
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )
    except Exception as e:
        print(
            "TELEGRAM ERROR:",
            e,
            flush=True
        )


# =========================================================
# CONNECTION ERROR DETECTION
# =========================================================

def is_connection_error(error):
    text = str(error).lower()

    connection_words = (
        "timed out",
        "timeout",
        "failed to connect",
        "socket",
        "websocket",
        "not connected",
        "connection",
        "disconnected",
        "synchronize",
        "synchronization"
    )

    return any(
        word in text
        for word in connection_words
    )


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(df):
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    close = df["close"].astype(float).to_numpy()

    n = len(df)

    if n < 5:
        return [], []

    psar = [0.0] * n
    bull = [True] * n

    is_bull = close[1] >= close[0]
    af = PSAR_STEP

    if is_bull:
        psar[0] = low[0]
        ep = high[0]
    else:
        psar[0] = high[0]
        ep = low[0]

    bull[0] = is_bull

    for i in range(1, n):
        value = (
            psar[i - 1]
            + af * (ep - psar[i - 1])
        )

        if is_bull:
            value = min(
                value,
                low[i - 1]
            )

            if i > 1:
                value = min(
                    value,
                    low[i - 2]
                )

            if low[i] < value:
                is_bull = False
                value = ep
                ep = low[i]
                af = PSAR_STEP

            elif high[i] > ep:
                ep = high[i]

                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        else:
            value = max(
                value,
                high[i - 1]
            )

            if i > 1:
                value = max(
                    value,
                    high[i - 2]
                )

            if high[i] > value:
                is_bull = True
                value = ep
                ep = high[i]
                af = PSAR_STEP

            elif low[i] < ep:
                ep = low[i]

                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

        psar[i] = value
        bull[i] = is_bull

    return psar, bull


# =========================================================
# M5 DATA
# =========================================================

async def get_closed_m5(account):
    candles = await account.get_historical_candles(
        SYMBOL,
        "5m",
        None,
        120
    )

    if not candles or len(candles) < 10:
        return None

    df = pd.DataFrame(candles)

    for col in [
        "open",
        "high",
        "low",
        "close"
    ]:
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

    if len(df) < 10:
        return None

    # Posledna sviecka moze byt este otvorena
    # preto ju nepouzivame
    df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


# =========================================================
# PSAR FLIP
# =========================================================

def get_flip(df):
    psar, bull = psar_values(df)

    if len(bull) < 2:
        return None, None

    # PSAR NAD -> POD cenu = BUY
    if (
        not bull[-2]
        and bull[-1]
    ):
        return (
            "BUY",
            float(psar[-1])
        )

    # PSAR POD -> NAD cenu = SELL
    if (
        bull[-2]
        and not bull[-1]
    ):
        return (
            "SELL",
            float(psar[-1])
        )

    return (
        None,
        float(psar[-1])
    )


# =========================================================
# POZICIE
# =========================================================

async def symbol_positions(connection):
    positions = await connection.get_positions()

    return [
        position
        for position in positions
        if position.get("symbol") == SYMBOL
    ]


# =========================================================
# SYMBOL INFO
# =========================================================

async def market_info(connection):
    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    price = await connection.get_symbol_price(
        SYMBOL
    )

    digits = int(
        spec.get("digits", 2)
    )

    point = float(
        spec.get("tickSize")
        or (10 ** (-digits))
    )

    return (
        price,
        digits,
        point
    )


# =========================================================
# OTVORENIE OBCHODU
# =========================================================

async def open_trade(
    connection,
    side,
    psar
):
    price, digits, point = await market_info(
        connection
    )

    if side == "BUY":
        entry = float(
            price["ask"]
        )

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

    else:
        entry = float(
            price["bid"]
        )

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

    try:
        if side == "BUY":
            await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

        else:
            await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

    except Exception as error:
        # Connection chybu posleme vyssie,
        # aby sa spustil reconnect.
        if is_connection_error(error):
            raise

        # Invalid stops = obnovime cenu
        # a skusime obchod este raz.
        if "invalid stops" not in str(error).lower():
            raise

        print(
            "INVALID STOPS - RETRY",
            flush=True
        )

        await asyncio.sleep(1)

        price = await connection.get_symbol_price(
            SYMBOL
        )

        if side == "BUY":
            entry = float(
                price["ask"]
            )

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

            await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

        else:
            entry = float(
                price["bid"]
            )

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

            await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

    print(
        f"OPEN {side} {SYMBOL} "
        f"LOT={LOT_SIZE} "
        f"ENTRY={entry} "
        f"SL={sl} "
        f"TP={tp}",
        flush=True
    )

    telegram(
        "RIObot GOLD\n\n"
        f"{side} {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Entry: {entry:.2f}\n"
        f"SL: {sl:.2f}\n"
        f"TP: {tp:.2f}\n"
        f"M5 PSAR: {psar:.2f}"
    )


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):
    positions = await symbol_positions(
        connection
    )

    if not positions:
        return

    price, digits, point = await market_info(
        connection
    )

    for position in positions:
        position_id = position.get("id")
        side = position.get("type")

        entry = float(
            position.get(
                "openPrice",
                0
            )
        )

        current_sl = position.get(
            "stopLoss"
        )

        tp = position.get(
            "takeProfit"
        )

        # BUY
        if side == "POSITION_TYPE_BUY":
            current = float(
                price["bid"]
            )

            profit_points = (
                current - entry
            ) / point

            new_sl = round(
                entry
                + BE_LOCK * point,
                digits
            )

            sl_ok = (
                current_sl is None
                or float(current_sl) < new_sl
            )

            if (
                profit_points >= BE_TRIGGER
                and sl_ok
            ):
                await connection.modify_position(
                    position_id,
                    new_sl,
                    tp
                )

                print(
                    f"BE BUY -> {new_sl}",
                    flush=True
                )

                telegram(
                    f"BE BUY {SYMBOL}\n"
                    f"SL -> {new_sl:.2f}"
                )

        # SELL
        elif side == "POSITION_TYPE_SELL":
            current = float(
                price["ask"]
            )

            profit_points = (
                entry - current
            ) / point

            new_sl = round(
                entry
                - BE_LOCK * point,
                digits
            )

            sl_ok = (
                current_sl is None
                or float(current_sl) > new_sl
            )

            if (
                profit_points >= BE_TRIGGER
                and sl_ok
            ):
                await connection.modify_position(
                    position_id,
                    new_sl,
                    tp
                )

                print(
                    f"BE SELL -> {new_sl}",
                    flush=True
                )

                telegram(
                    f"BE SELL {SYMBOL}\n"
                    f"SL -> {new_sl:.2f}"
                )


# =========================================================
# BOT SESSION
# =========================================================

async def bot_session(api):
    connection = None

    try:
        account = (
            await api.metatrader_account_api.get_account(
                M_ACC
            )
        )

        if account.state != "DEPLOYED":
            print(
                "DEPLOYING METAAPI ACCOUNT...",
                flush=True
            )

            await account.deploy()

        connection = account.get_rpc_connection()

        print(
            "CONNECTING METAAPI...",
            flush=True
        )

        await connection.connect()

        print(
            "WAITING FOR SYNCHRONIZATION...",
            flush=True
        )

        await connection.wait_synchronized()

        print(
            "RIObot GOLD CONNECTED",
            flush=True
        )

        telegram(
            "RIObot GOLD START / CONNECTED\n\n"
            f"Symbol: {SYMBOL}\n"
            f"Lot: {LOT_SIZE}\n"
            "Timeframe: M5\n"
            "Strategy: PSAR FLIP\n"
            f"TP: {TP_POINTS}\n"
            f"SL: {SL_POINTS}\n"
            f"BE: +{BE_TRIGGER} -> +{BE_LOCK}\n"
            "BTCUSD: OFF"
        )

        last_candle = None
        connection_errors = 0

        while True:
            try:
                # -----------------------------
                # BREAK EVEN
                # -----------------------------
                await manage_be(
                    connection
                )

                # -----------------------------
                # M5 DATA
                # -----------------------------
                df = await get_closed_m5(
                    account
                )

                if (
                    df is not None
                    and len(df) >= 10
                ):
                    candle_id = str(
                        df.iloc[-1].get(
                            "time"
                        )
                    )

                    # Kazdu uzavretu M5
                    # spracujeme iba raz
                    if candle_id != last_candle:
                        signal, psar = get_flip(
                            df
                        )

                        positions = (
                            await symbol_positions(
                                connection
                            )
                        )

                        print(
                            f"{SYMBOL} "
                            f"M5={candle_id} "
                            f"FLIP={signal} "
                            f"PSAR={psar}",
                            flush=True
                        )

                        # Candle oznacime ako spracovanu
                        # az po uspesnych MetaApi volaniach
                        last_candle = candle_id

                        # Max 1 XAUUSD pozicia
                        if (
                            not positions
                            and signal in (
                                "BUY",
                                "SELL"
                            )
                        ):
                            await open_trade(
                                connection,
                                signal,
                                psar
                            )

                # Uspesny cyklus =
                # reset connection errors
                connection_errors = 0

            except Exception as e:
                print(
                    "LOOP ERROR XAUUSD:",
                    e,
                    flush=True
                )

                if is_connection_error(e):
                    connection_errors += 1

                    print(
                        "METAAPI CONNECTION FAILURE "
                        f"{connection_errors}/"
                        f"{MAX_CONNECTION_ERRORS}",
                        flush=True
                    )

                    if (
                        connection_errors
                        >= MAX_CONNECTION_ERRORS
                    ):
                        print(
                            "FORCING FULL METAAPI "
                            "RECONNECT...",
                            flush=True
                        )

                        raise ConnectionError(
                            "MetaApi connection lost - "
                            "forcing reconnect"
                        ) from e

                else:
                    # Nie je to connection chyba.
                    # Bot pokracuje dalej.
                    connection_errors = 0

            await asyncio.sleep(
                LOOP_SECONDS
            )

    finally:
        if connection is not None:
            try:
                print(
                    "CLOSING OLD METAAPI "
                    "CONNECTION...",
                    flush=True
                )

                await connection.close()

            except Exception as e:
                print(
                    "CONNECTION CLOSE ERROR:",
                    e,
                    flush=True
                )


# =========================================================
# MAIN / FULL RECONNECT
# =========================================================

async def main():
    if not M_TOKEN:
        raise RuntimeError(
            "M_TOKEN is missing"
        )

    if not M_ACC:
        raise RuntimeError(
            "M_ACC is missing"
        )

    keep_alive()

    while True:
        try:
            # Pri kazdom novom session vytvorime
            # novy MetaApi objekt.
            api = MetaApi(
                M_TOKEN
            )

            await bot_session(
                api
            )

        except Exception as e:
            print(
                "METAAPI SESSION ERROR:",
                e,
                flush=True
            )

            telegram(
                "RIObot GOLD\n"
                "MetaApi spojenie vypadlo.\n"
                "Robim FULL RECONNECT.\n"
                f"Retry za {RECONNECT_SECONDS}s."
            )

            print(
                f"RECONNECT IN "
                f"{RECONNECT_SECONDS}s...",
                flush=True
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
