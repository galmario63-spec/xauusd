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

COMMENT = "RIObot GOLD M5 PSAR BE"


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK / RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD M5 ACTIVE"


def run_server():
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )


def keep_alive():
    thread = Thread(
        target=run_server,
        daemon=True
    )

    thread.start()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):

    if not T_TOKEN or not T_CHAT:
        return

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{T_TOKEN}/sendMessage"
        )

        requests.post(
            url,
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

    required = [
        "open",
        "high",
        "low",
        "close"
    ]

    for col in required:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=required
    ).reset_index(drop=True)

    if len(df) < 10:
        return None

    # Ignorujeme aktualne otvorenu M5 sviecku
    df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


# =========================================================
# PSAR FLIP
# =========================================================

def get_flip(df):

    psar, bull = psar_values(df)

    if len(bull) < 2:
        return None, None

    # PSAR sa prehodil NAD -> POD cenu = BUY
    if not bull[-2] and bull[-1]:

        return (
            "BUY",
            float(psar[-1])
        )

    # PSAR sa prehodil POD -> NAD cenu = SELL
    if bull[-2] and not bull[-1]:

        return (
            "SELL",
            float(psar[-1])
        )

    return (
        None,
        float(psar[-1])
    )


# =========================================================
# OTVORENE XAUUSD POZICIE
# =========================================================

async def get_positions(connection):

    positions = await connection.get_positions()

    return [
        position
        for position in positions
        if position.get("symbol") == SYMBOL
    ]


# =========================================================
# OTVORENIE OBCHODU
# =========================================================

async def open_trade(
    connection,
    side,
    psar
):

    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    digits = int(
        spec.get("digits", 2)
    )

    point = float(
        spec.get("tickSize")
        or 10 ** (-digits)
    )

    async def calculate_prices():

        price_data = await connection.get_symbol_price(
            SYMBOL
        )

        if side == "BUY":

            entry = float(
                price_data["ask"]
            )

            sl = round(
                entry - SL_POINTS * point,
                digits
            )

            tp = round(
                entry + TP_POINTS * point,
                digits
            )

        else:

            entry = float(
                price_data["bid"]
            )

            sl = round(
                entry + SL_POINTS * point,
                digits
            )

            tp = round(
                entry - TP_POINTS * point,
                digits
            )

        return entry, sl, tp

    entry, sl, tp = await calculate_prices()

    async def send_order():

        if side == "BUY":

            return await connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {
                    "comment": COMMENT
                }
            )

        return await connection.create_market_sell_order(
            SYMBOL,
            LOT_SIZE,
            sl,
            tp,
            {
                "comment": COMMENT
            }
        )

    try:

        await send_order()

    except Exception as error:

        if "Invalid stops" not in str(error):

            raise

        print(
            "INVALID STOPS - RETRY",
            flush=True
        )

        await asyncio.sleep(1)

        entry, sl, tp = await calculate_prices()

        await send_order()

    print(
        f"OPEN {side} {SYMBOL} "
        f"LOT={LOT_SIZE} "
        f"ENTRY={entry} "
        f"SL={sl} "
        f"TP={tp}",
        flush=True
    )

    telegram(
        f"RIObot GOLD\n\n"
        f"{side} {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Entry: {entry:.2f}\n"
        f"SL: {sl:.2f}\n"
        f"TP: {tp:.2f}\n"
        f"PSAR: {psar:.2f}"
    )


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):

    positions = await get_positions(
        connection
    )

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
        or 10 ** (-digits)
    )

    for position in positions:

        try:

            position_id = position.get("id")
            side = position.get("type")

            entry = float(
                position.get("openPrice", 0)
            )

            current_sl = position.get(
                "stopLoss"
            )

            tp = position.get(
                "takeProfit"
            )

            # ==============================
            # BUY
            # ==============================

            if side == "POSITION_TYPE_BUY":

                current = float(
                    price_data["bid"]
                )

                profit_points = (
                    current - entry
                ) / point

                new_sl = round(
                    entry + BE_LOCK * point,
                    digits
                )

                if current_sl is None:

                    sl_needs_change = True

                else:

                    sl_needs_change = (
                        float(current
