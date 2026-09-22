import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD - NASTAVENIA
# =========================================================

SYMBOL = "XAUUSD"

# CENTOVÝ ÚČET
LOT_SIZE = 1.00

# PARABOLIC SAR - M5
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# TP / SL
# XAUUSD: 100 bodov ~= 1.00 USD pohybu ceny
TP_POINTS = 500.0       # +5.00 USD pohyb ceny
SL_POINTS = 1000.0      # -10.00 USD pohyb ceny

# BREAK EVEN
BE_TRIGGER = 250.0      # pri +2.50 USD pohybe
BE_LOCK = 100.0         # zamkne +1.00 USD od vstupu

COMMENT = "RIObot GOLD M5 PSAR BE"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
MAX_CONNECTION_ERRORS = 2


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK / RENDER KEEP ALIVE
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD ACTIVE"


def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    thread = Thread(target=run_server)
    thread.daemon = True
    thread.start()


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
        print(f"TELEGRAM ERROR: {e}", flush=True)


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(df):
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()

    length = len(df)

    if length < 3:
        return [None] * length

    psar = [None] * length

    bull = True
    af = PSAR_STEP
    ep = high[0]
    sar = low[0]

    psar[0] = sar

    for i in range(1, length):

        previous_sar = sar
        sar = previous_sar + af * (ep - previous_sar)

        if bull:

            if i >= 2:
                sar = min(sar, low[i - 1], low[i - 2])
            else:
                sar = min(sar, low[i - 1])

            if low[i] < sar:
                bull = False
                sar = ep
                ep = low[i]
                af = PSAR_STEP
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + PSAR_STEP, PSAR_MAX)

        else:

            if i >= 2:
                sar = max(sar, high[i - 1], high[i - 2])
            else:
                sar = max(sar, high[i - 1])

            if high[i] > sar:
                bull = True
                sar = ep
                ep = high[i]
                af = PSAR_STEP
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + PSAR_STEP, PSAR_MAX)

        psar[i] = sar

    return psar


# =========================================================
# M5 CANDLES
# =========================================================

async def get_closed_m5(account):

    candles = await account.get_historical_candles(
        SYMBOL,
        "5m",
        None,
        120
    )

    if not candles or len(candles) < 5:
        return None

    df = pd.DataFrame(candles)

    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    # Posledná sviečka môže byť ešte otvorená.
    # RIObot používa iba zatvorené M5 sviečky.
    if len(df) > 1:
        df = df.iloc[:-1].copy()

    if len(df) < 5:
        return None

    df["psar"] = psar_values(df)

    return df


# =========================================================
# PSAR FLIP
# =========================================================

def get_flip(df):

    if df is None or len(df) < 3:
        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

    previous_close = float(previous["close"])
    current_close = float(current["close"])

    previous_psar = float(previous["psar"])
    current_psar = float(current["psar"])

    # PSAR bol nad cenou a prešiel pod cenu = BUY
    if previous_psar > previous_close and current_psar < current_close:
        return "BUY"

    # PSAR bol pod cenou a prešiel nad cenu = SELL
    if previous_psar < previous_close and current_psar > current_close:
        return "SELL"

    return None


# =========================================================
# POZÍCIE
# =========================================================

async def symbol_positions(connection):

    positions = await connection.get_positions()

    result = []

    for position in positions:
        symbol = position.get("symbol", "")

        if symbol.upper() == SYMBOL.upper():
            result.append(position)

    return result


# =========================================================
# SYMBOL INFO
# =========================================================

async def market_info(connection):

    specification = await connection.get_symbol_specification(SYMBOL)
    price = await connection.get_symbol_price(SYMBOL)

    digits = int(specification.get("digits", 2))

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

    point, digits, bid, ask = await market_info(connection)

    if side == "BUY":

        entry = ask

        sl = round(
            entry - (SL_POINTS * point),
            digits
        )

        tp = round(
            entry + (TP_POINTS * point),
            digits
        )

    else:

        entry = bid

        sl = round(
            entry + (SL_POINTS * point),
            digits
        )

        tp = round(
            entry - (TP_POINTS * point),
            digits
        )

    print(
        f"OPEN {side} {SYMBOL} "
        f"LOT={LOT_SIZE} ENTRY={entry} SL={sl} TP={tp}",
        flush=True
    )

    try:

        if side == "BUY":

            result = await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

        else:

            result = await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

    except Exception as e:

        # Ak broker odmietne stops, obnovíme cenu
        # a skúsime objednávku ešte raz.
        if "Invalid stops" not in str(e):
            raise

        print(
            "INVALID STOPS - REFRESH PRICE AND RETRY",
            flush=True
        )

        await asyncio.sleep(1)

        point, digits, bid, ask = await market_info(connection)

        if side == "BUY":

            entry = ask
            sl = round(entry - SL_POINTS * point, digits)
            tp = round(entry + TP_POINTS * point, digits)

            result = await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

        else:

            entry = bid
            sl = round(entry + SL_POINTS * point, digits)
            tp = round(entry - TP_POINTS * point, digits)

            result = await connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

    telegram(
        f"RIObot GOLD TRADE\n\n"
        f"{side} {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n\n"
        f"BE: +2.50 -> +1.00"
    )

    return result


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):

    positions = await symbol_positions(connection)

    if not positions:
        return

    point, digits, bid, ask = await market_info(connection)

    for position in positions:

        position_id = position.get("id")

        side = position.get("type", "").upper()

        entry = float(position.get("openPrice", 0))

        current_sl = position.get("stopLoss")
        current_tp = position.get("takeProfit")

        if current_sl is not None:
            current_sl = float(current_sl)

        if current_tp is not None:
            current_tp = float(current_tp)

        # =================================================
        # BUY
        # =================================================

        if side == "POSITION_TYPE_BUY" or side == "BUY":

            profit_points = (bid - entry) / point

            if profit_points >= BE_TRIGGER:

                new_sl = round(
                    entry + (BE_LOCK * point),
                    digits
                )

                # Posuň SL iba raz smerom do zisku
                if current_sl is None or current_sl < new_sl:

                    await connection.modify_position(
                        position_id,
                        new_sl,
                        current_tp
                    )

                    print(
                        f"BE BUY -> SL {new_sl}",
                        flush=True
                    )

                    telegram(
                        f"RIObot GOLD BE\n\n"
                        f"BUY {SYMBOL}\n"
                        f"Profit reached +2.50\n"
                        f"SL locked at +1.00\n"
                        f"New SL: {new_sl}"
                    )

        # =================================================
        # SELL
        # =================================================

        elif side == "POSITION_TYPE_SELL" or side == "SELL":

            profit_points = (entry - ask) / point

            if profit_points >= BE_TRIGGER:

                new_sl = round(
                    entry - (BE_LOCK * point),
                    digits
                )

                # Pri SELL musí nový SL ísť nižšie
                if current_sl is None or current_sl > new_sl:

                    await connection.modify_position(
                        position_id,
                        new_sl,
                        current_tp
                    )

                    print(
                        f"BE SELL -> SL {new_sl}",
                        flush=True
                    )

                    telegram(
                        f"RIObot GOLD BE\n\n"
                        f"SELL {SYMBOL}\n"
                        f"Profit reached +2.50\n"
                        f"SL locked at +1.00\n"
                        f"New SL: {new_sl}"
                    )


# =========================================================
# BOT SESSION
# =========================================================

async def bot_session(api):

    account = await api.metatrader_account_api.get_account(M_ACC)

    print("CONNECTING METAAPI...", flush=True)

    connection = account.get_rpc_connection()

    await connection.connect()

    print("WAITING FOR SYNCHRONIZATION...", flush=True)

    await connection.wait_synchronized()

    print("RIObot GOLD CONNECTED", flush=True)

    telegram(
        f"RIObot GOLD START / CONNECTED\n\n"
        f"Symbol: {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Timeframe: M5\n"
        f"Strategy: PSAR FLIP\n"
        f"TP: +5.00 price move\n"
        f"SL: -10.00 price move\n"
        f"BE: +2.50 -> +1.00\n"
        f"BTCUSD: OFF"
    )

    last_candle_time = None
    connection_errors = 0

    while True:

        try:

            # BE kontrolujeme každých 10 sekúnd
            await manage_be(connection)

            df = await get_closed_m5(account)

            if df is None:
                await asyncio.sleep(LOOP_SECONDS)
                continue

            candle = df.iloc[-1]

            candle_time = candle.get("time")

            # Entry kontrolujeme iba raz na novú
            # zatvorenú M5 sviečku.
            if candle_time != last_candle_time:

                last_candle_time = candle_time

                flip = get_flip(df)

                print(
                    f"{SYMBOL} M5={candle_time} "
                    f"FLIP={flip} "
                    f"PSAR={candle['psar']}",
                    flush=True
                )

                positions = await symbol_positions(connection)

                # Maximálne jedna XAUUSD pozícia
                if not positions and flip in ["BUY", "SELL"]:

                    await open_trade(
                        connection,
                        flip
                    )

            connection_errors = 0

        except Exception as e:

            connection_errors += 1

            print(
                f"LOOP ERROR {SYMBOL}: {e}",
                flush=True
            )

            print(
                f"METAAPI CONNECTION FAILURE "
                f"{connection_errors}/{MAX_CONNECTION_ERRORS}",
                flush=True
            )

            if connection_errors >= MAX_CONNECTION_ERRORS:

                print(
                    "RECONNECTING METAAPI...",
                    flush=True
                )

                raise

        await asyncio.sleep(LOOP_SECONDS)


# =========================================================
# MAIN
# =========================================================

async def main():

    if not M_TOKEN:
        raise RuntimeError("M_TOKEN is missing")

    if not M_ACC:
        raise RuntimeError("M_ACC is missing")

    keep_alive()

    # Dôležité:
    # žiadny region
    # žiadny clientId
    api = MetaApi(M_TOKEN)

    while True:

        try:

            await bot_session(api)

        except Exception as e:

            print(
                f"BOT SESSION ERROR: {e}",
                flush=True
            )

            telegram(
                f"RIObot GOLD CONNECTION ERROR\n\n"
                f"Reconnect in {RECONNECT_SECONDS} seconds."
            )

            await asyncio.sleep(RECONNECT_SECONDS)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
