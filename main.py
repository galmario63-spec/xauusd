import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# NASTAVENIA - XAUUSD
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 0.50

# PARABOLIC SAR M5
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# TP / SL
TP_POINTS = 750.0
SL_POINTS = 500.0

# BREAK EVEN
BE_TRIGGER = 300.0
BE_LOCK = 100.0

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15

COMMENT = "RIObot GOLD M5 PSAR BE"


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK - RENDER KEEP ALIVE
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD M5 active"


def run_server():
    port = int(os.getenv("PORT", "10000"))
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
            "Telegram error:",
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

            # FLIP BUY -> SELL
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

            # FLIP SELL -> BUY
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

    # Posledna sviecka sa este tvori,
    # preto ju robot ignoruje.
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

        return (
            "BUY",
            float(psar[-1])
        )

    # Bodky boli POD cenou
    # a presli NAD cenu = SELL
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
# XAUUSD POZICIE
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

async def open_trade(
    connection,
    side,
    psar
):

    spec = (
        await connection
        .get_symbol_specification(SYMBOL)
    )

    price_data = (
        await connection
        .get_symbol_price(SYMBOL)
    )

    digits = int(
        spec.get("digits", 2)
    )

    point = float(
        spec.get("tickSize")
        or (10 ** (-digits))
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

    async def send_order():

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

    try:

        await send_order()

    except Exception as first_error:

        # Pri rychlom pohybe ceny
        # obnovime cenu a skusime 1x znova.
        if "Invalid stops" not in str(first_error):
            raise

        await asyncio.sleep(1)

        price_data = (
            await connection
            .get_symbol_price(SYMBOL)
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

        await send_order()

    telegram(
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

    spec
