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

SYMBOL = "BTCUSD"
LOT_SIZE = 0.30

# PARABOLIC SAR M5
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# SL / TP
TP_POINTS = 3000.0
SL_POINTS = 3000.0

# BREAK EVEN
BE_TRIGGER = 1000.0
BE_LOCK = 200.0

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15

COMMENT = "RIObot M5 PSAR flip BE"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK - RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot M5 active"


def run_server():
    port = int(os.getenv("PORT", "10000"))
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
        url = f"https://api.telegram.org/bot{T_TOKEN}/sendMessage"

        requests.post(
            url,
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )

    except Exception as e:
        print("Telegram error:", e, flush=True)


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

        value = psar[i - 1] + af * (ep - psar[i - 1])

        if is_bull:

            value = min(value, low[i - 1])

            if i > 1:
                value = min(value, low[i - 2])

            # FLIP DO SELL
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

            value = max(value, high[i - 1])

            if i > 1:
                value = max(value, high[i - 2])

            # FLIP DO BUY
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
# UZAVRETE M5 SVIECKY
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

    for col in ["open", "high", "low", "close"]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    if len(df) < 10:
        return None

    # Posledna M5 sviecka sa este tvori.
    # Robot ju preto ignoruje.
    df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


# =========================================================
# M5 PSAR FLIP
# =========================================================

def get_flip(df):

    psar, bull = psar_values(df)

    if len(bull) < 2:
        return None, None

    # Bodky boli NAD cenou
    # a presli POD cenu = BUY
    if not bull[-2] and bull[-1]:
        return "BUY", float(psar[-1])

    # Bodky boli POD cenou
    # a presli NAD cenu = SELL
    if bull[-2] and not bull[-1]:
        return "SELL", float(psar[-1])

    return None, float(psar[-1])


# =========================================================
# POZICIE
# =========================================================

async def symbol_positions(connection):

    positions = await connection.get_positions()

    return [
        p for p in positions
        if p.get("symbol") == SYMBOL
    ]


# =========================================================
# OTVORENIE OBCHODU
# =========================================================

async def open_trade(connection, side, psar):

    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    price_data = await connection.get_symbol_price(
        SYMBOL
    )

    digits = int(
        spec.get("digits", 2)
    )

    point = float(
        spec.get("tickSize")
        or (10 ** (-digits))
    )

    if side == "BUY":

        entry = float(price_data["ask"])

        sl = round(
            entry - SL_POINTS * point,
            digits
        )

        tp = round(
            entry + TP_POINTS * point,
            digits
        )

    else:

        entry = float(price_data["bid"])

        sl = round(
            entry + SL_POINTS * point,
            digits
        )

        tp = round(
            entry - TP_POINTS * point,
            digits
        )

    try:

        if side == "BUY":

            await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {"comment": COMMENT}
            )

        else:

            await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {"comment": COMMENT}
            )

    except Exception as first_error:

        # Pri rychlom pohybe ceny moze broker
        # odmietnut povodne SL/TP.
        if "Invalid stops" not in str(first_error):
            raise

        await asyncio.sleep(1)

        price_data = await connection.get_symbol_price(
            SYMBOL
        )

        if side == "BUY":

            entry = float(price_data["ask"])

            sl = round(
                entry - SL_POINTS * point,
                digits
            )

            tp = round(
                entry + TP_POINTS * point,
                digits
            )

            await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {"comment": COMMENT}
            )

        else:

            entry = float(price_data["bid"])

            sl = round(
                entry + SL_POINTS * point,
                digits
            )

            tp = round(
                entry - TP_POINTS * point,
                digits
            )

            await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {"comment": COMMENT}
            )

    telegram(
        f"{side} {SYMBOL}\n"
        f"Entry: {entry:.2f}\n"
        f"SL: {sl:.2f}\n"
        f"TP: {tp:.2f}\n"
        f"M5 PSAR: {psar:.2f}"
    )


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):

    positions = await symbol_positions(connection)

    if not positions:
        return

    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    price_data = await connection.get_symbol_price(
        SYMBOL
    )

    digits = int(
        spec.get("digits", 2)
    )

    point = float(
        spec.get("tickSize")
        or (10 ** (-digits))
    )

    for p in positions:

        pos_id = p.get("id")
        side = p.get("type")

        entry = float(
            p.get("openPrice", 0)
        )

        current_sl = p.get("stopLoss")
        tp = p.get("takeProfit")

        try:

            # BUY
            if side == "POSITION_TYPE_BUY":

                current = float(
                    price_data["bid"]
                )

                profit_points = (
                    current - entry
                ) / point

                be_sl = round(
                    entry + BE_LOCK * point,
                    digits
                )

                sl_ok = (
                    current_sl is None
                    or float(current_sl) < be_sl
                )

                if (
                    profit_points >= BE_TRIGGER
                    and sl_ok
                ):

                    await connection.modify_position(
                        pos_id,
                        be_sl,
                        tp
                    )

                    telegram(
                        f"BE BUY {SYMBOL}\n"
                        f"SL -> {be_sl:.2f}"
                    )

            # SELL
            elif side == "POSITION_TYPE_SELL":

                current = float(
                    price_data["ask"]
                )

                profit_points = (
                    entry - current
                ) / point

                be_sl = round(
                    entry - BE_LOCK * point,
                    digits
                )

                sl_ok = (
                    current_sl is None
                    or float(current_sl) > be_sl
                )

                if (
                    profit_points >= BE_TRIGGER
                    and sl_ok
                ):

                    await connection.modify_position(
                        pos_id,
                        be_sl,
                        tp
                    )

                    telegram(
                        f"BE SELL {SYMBOL}\n"
                        f"SL -> {be_sl:.2f}"
                    )

        except Exception as e:

            print(
                "BE error:",
                e,
                flush=True
            )


# =========================================================
# BOT SESSION
# =========================================================

async def bot_session(api):

    account = await api.metatrader_account_api.get_account(
        M_ACC
    )

    if account.state != "DEPLOYED":
        await account.deploy()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    telegram(
        "RIObot START / CONNECTED\n"
        f"{SYMBOL} lot {LOT_SIZE}\n"
        "M5 PSAR FLIP entry\n"
        "M1 filter OFF\n"
        f"TP {TP_POINTS} | SL {SL_POINTS}\n"
        f"BE +{BE_TRIGGER} -> +{BE_LOCK}"
    )

    last_candle = None

    try:

        while True:

            try:

                # Kontrola Break Even
                await manage_be(connection)

                # Nacitanie M5
                df = await get_closed_m5(account)

                if df is not None and len(df) >= 10:

                    candle_id = str(
                        df.iloc[-1].get("time")
                    )

                    # Kazdu uzavretu M5 sviecku
                    # spracujeme iba raz.
                    if candle_id != last_candle:

                        last_candle = candle_id

                        signal, psar = get_flip(df)

                        positions = await symbol_positions(
                            connection
                        )

                        print(
                            f"M5={candle_id} "
                            f"FLIP={signal} "
                            f"PSAR={psar}",
                            flush=True
                        )

                        # Iba jedna BTCUSD pozicia naraz.
                        if (
                            not positions
                            and signal in ("BUY", "SELL")
                        ):

                            await open_trade(
                                connection,
                                signal,
                                psar
                            )

            except Exception as e:

                print(
                    "LOOP ERROR:",
                    e,
                    flush=True
                )

                await asyncio.sleep(5)

            await asyncio.sleep(
                LOOP_SECONDS
            )

    finally:

        try:
            await connection.close()

        except Exception:
            pass


# =========================================================
# MAIN + RECONNECT
# =========================================================

async def main():

    if not M_TOKEN or not M_ACC:

        raise RuntimeError(
            "Missing M_TOKEN or M_ACC"
        )

    keep_alive()

    api = MetaApi(M_TOKEN)

    while True:

        try:

            await bot_session(api)

        except Exception as e:

            print(
                "CONNECTION ERROR:",
                e,
                flush=True
            )

            telegram(
                "RIObot: MetaApi connection lost.\n"
                f"Reconnect za {RECONNECT_SECONDS}s..."
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


if __name__ == "__main__":
    asyncio.run(main())
