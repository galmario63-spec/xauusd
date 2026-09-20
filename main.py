import asyncio
import os
import traceback
from datetime import datetime
from flask import Flask
from threading import Thread

import pandas as pd
import requests
from metaapi_cloud_sdk import MetaApi


# =========================================================
# NASTAVENIA
# =========================================================

SYMBOL_REQUEST = "BTCUSD"

# CENTOVÝ ÚČET
LOT_SIZE = 0.30

# PSAR
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# EMA FILTER - M5
EMA_PERIOD = 50

# SL / TP - 1:1
TP_POINTS = 1500.0
SL_POINTS = 1500.0

# BREAK EVEN
BE_TRIGGER = 500.0
BE_LOCK = 100.0

# OSTATNÉ
COMMENT = "Riobot 5M+1M BE+PSAR"
LOOP_SECONDS = 10

# Po koľkých chybách spojenia spraviť reconnect
MAX_CONNECTION_ERRORS = 2


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# LOG
# =========================================================

def log(message):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot is running"


@app.route("/health")
def health():
    return "OK"


def run_flask():
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):
    if not T_TOKEN or not T_CHAT:
        log(message)
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
        log(f"Telegram chyba: {e}")


# =========================================================
# PSAR
# =========================================================

def calculate_psar(df, step=0.02, max_af=0.20):
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()

    n = len(df)

    if n < 5:
        return None, None

    psar = [0.0] * n

    bull = True
    af = step
    ep = high[0]

    psar[0] = low[0]

    for i in range(1, n):
        previous_psar = psar[i - 1]

        if bull:
            psar[i] = (
                previous_psar
                + af * (ep - previous_psar)
            )

            if i >= 2:
                psar[i] = min(
                    psar[i],
                    low[i - 1],
                    low[i - 2]
                )
            else:
                psar[i] = min(
                    psar[i],
                    low[i - 1]
                )

            if low[i] < psar[i]:
                bull = False
                psar[i] = ep
                ep = low[i]
                af = step

            elif high[i] > ep:
                ep = high[i]
                af = min(
                    af + step,
                    max_af
                )

        else:
            psar[i] = (
                previous_psar
                + af * (ep - previous_psar)
            )

            if i >= 2:
                psar[i] = max(
                    psar[i],
                    high[i - 1],
                    high[i - 2]
                )
            else:
                psar[i] = max(
                    psar[i],
                    high[i - 1]
                )

            if high[i] > psar[i]:
                bull = True
                psar[i] = ep
                ep = high[i]
                af = step

            elif low[i] < ep:
                ep = low[i]
                af = min(
                    af + step,
                    max_af
                )

    return psar[-1], bull


# =========================================================
# EMA
# =========================================================

def calculate_ema(df, period=50):
    return (
        df["close"]
        .ewm(
            span=period,
            adjust=False
        )
        .mean()
    )


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(connection, symbol):
    return await asyncio.wait_for(
        connection.get_symbol_specification(symbol),
        timeout=30
    )


# =========================================================
# POZÍCIE
# =========================================================

async def get_positions(connection, symbol):
    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=30
    )

    return [
        p
        for p in positions
        if p.get("symbol") == symbol
    ]


# =========================================================
# UZAVRETÉ SVIEČKY
# =========================================================

async def get_closed_candles(
    account,
    symbol,
    timeframe,
    limit=100,
    minimum=10
):
    candles = await asyncio.wait_for(
        account.get_historical_candles(
            symbol=symbol,
            timeframe=timeframe,
            start_time=None,
            limit=limit
        ),
        timeout=40
    )

    if not candles:
        log(f"{timeframe}: žiadne dáta")
        return None

    df = pd.DataFrame(candles)

    if df.empty:
        return None

    if "time" in df.columns:
        df = df.sort_values("time")

    required = [
        "open",
        "high",
        "low",
        "close"
    ]

    if not all(
        col in df.columns
        for col in required
    ):
        log(f"{timeframe}: chýbajú OHLC dáta")
        return None

    for col in required:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(subset=required)

    if len(df) < minimum + 1:
        log(
            f"{timeframe}: málo dát "
            f"({len(df)})"
        )
        return None

    # Posledná sviečka sa ešte tvorí,
    # preto používame iba uzavreté sviečky.
    closed_df = df.iloc[:-1].copy()

    if len(closed_df) < minimum:
        return None

    return closed_df


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_break_even(
    connection,
    symbol
):
    positions = await get_positions(
        connection,
        symbol
    )

    if not positions:
        return

    spec = await get_symbol_info(
        connection,
        symbol
    )

    if not spec:
        return

    point = float(
        spec.get("point", 0.01)
    )

    digits = int(
        spec.get("digits", 2)
    )

    for position in positions:
        position_id = position.get("id")
        position_type = position.get("type")

        open_price = float(
            position.get("openPrice")
        )

        current_price = float(
            position.get("currentPrice")
        )

        current_sl = position.get("stopLoss")
        take_profit = position.get("takeProfit")

        # BUY
        if position_type == "POSITION_TYPE_BUY":
            profit_points = (
                current_price - open_price
            ) / point

            if profit_points >= BE_TRIGGER:
                new_sl = round(
                    open_price
                    + BE_LOCK * point,
                    digits
                )

                if (
                    current_sl is None
                    or float(current_sl) < new_sl
                ):
                    await asyncio.wait_for(
                        connection.modify_position(
                            position_id=position_id,
                            stop_loss=new_sl,
                            take_profit=take_profit
                        ),
                        timeout=30
                    )

                    telegram(
                        "🟢 BREAK EVEN – BUY\n\n"
                        f
