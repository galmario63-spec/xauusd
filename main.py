import asyncio
import os
import traceback
from flask import Flask
from threading import Thread

import pandas as pd
import requests
from metaapi_cloud_sdk import MetaApi


# =========================================================
# NASTAVENIA
# =========================================================

SYMBOL = "BTCUSD"
LOT_SIZE = 0.30

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_POINTS = 1500.0
SL_POINTS = 1500.0

BE_TRIGGER = 500.0
BE_LOCK = 100.0

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15

COMMENT = "Riobot M1 PSAR ONLY"


# =========================================================
# ENV
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
    return "RIObot M1 PSAR ONLY is running"


@app.route("/health")
def health():
    return "OK"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    thread = Thread(target=run_server, daemon=True)
    thread.start()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):

    if not T_TOKEN or not T_CHAT:
        print(message)
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
        print("Telegram chyba:", e)


# =========================================================
# PSAR
# =========================================================

def calculate_psar(df, step=0.02, max_af=0.20):

    if len(df) < 5:
        return None, None

    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    n = len(df)

    psar = [0.0] * n

    bull = close[1] >= close[0]

    af = step
    ep = high[0] if bull else low[0]

    psar[0] = low[0] if bull else high[0]

    for i in range(1, n):

        previous_psar = psar[i - 1]

        psar[i] = (
            previous_psar
            + af * (ep - previous_psar)
        )

        # BUY / BULL
        if bull:

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

        # SELL / BEAR
        else:

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

    return float(psar[-1]), bull


# =========================================================
# SVIEČKY
# =========================================================

async def get_closed_candles(
    account,
    symbol,
    timeframe="1m",
    limit=100
):

    candles = await account.get_historical_candles(
        symbol=symbol,
        timeframe=timeframe,
        start_time=None,
        limit=limit
    )

    if not candles or len(candles) < 11:
        raise Exception("Málo dát pre PSAR")

    df = pd.DataFrame(candles)

    if "time" in df.columns:
        df = df.sort_values("time")

    for column in ["open", "high", "low", "close"]:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    # Posledná sviečka sa ešte tvorí.
    # Obchodujeme iba podľa uzavretých M1 sviečok.
    df = df.iloc[:-1].copy()

    if len(df) < 10:
        raise Exception("Málo uzavretých M1 sviečok")

    return df


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(connection):

    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    if not spec:
        raise Exception(
            f"Symbol {SYMBOL} nenájdený"
        )

    return spec


def get_point(spec):

    # Najprv point, potom tickSize.
    value = (
        spec.get("point")
        or spec.get("tickSize")
        or 0.01
    )

    return float(value)


# =========================================================
# POZÍCIE
# =========================================================

async def get_positions(connection):

    positions = await connection.get_positions()

    return [
        position
        for position in positions
        if position.get("symbol") == SYMBOL
    ]


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_break_even(connection):

    positions = await get_positions(connection)

    if not positions:
        return

    spec = await get_symbol_info(connection)

    point = get_point(spec)
    digits = int(spec.get("digits", 2))

    price = await connection.get_symbol_price(
        SYMBOL
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in positions:

        position_id = position.get("id")
        position_type = position.get("type")

        open_price = float(
            position.get("openPrice")
        )

        current_sl = position.get("stopLoss")
        take_profit = position.get("takeProfit")

        # BUY
        if position_type == "POSITION_TYPE_BUY":

            profit_points = (
                bid - open_price
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            new_sl = round(
                open_price + BE_LOCK * point,
                digits
            )

            if (
                current_sl is not None
                and float(current_sl) >= new_sl
            ):
                continue

            await connection.modify_position(
                position_id=position_id,
                stop_loss=new_sl,
                take_profit=take_profit
            )

            telegram(
                "🟢 BREAK EVEN BUY\n"
                f"{SYMBOL}\n"
                f"SL → {new_sl}\n"
                f"Zamknuté: +{BE_LOCK:.0f} bodov"
            )

        # SELL
        elif position_type == "POSITION_TYPE_SELL":

            profit_points = (
                open_price - ask
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            new_sl = round(
                open_price - BE_LOCK * point,
                digits
            )

            if (
                current_sl is not None
                and float(current_sl) <= new_sl
            ):
                continue

            await connection.modify_position(
                position_id=position_id,
                stop_loss=new_sl,
                take_profit=take_profit
            )

            telegram(
                "🔴 BREAK EVEN SELL\n"
                f"{SYMBOL}\n"
                f"SL → {new_sl}\n"
                f"Zamknuté: +{BE_LOCK:.0f} bodov"
            )


# =========================================================
# M1 PSAR TRAILING - AŽ PO BE
# =========================================================

async def manage_psar_trailing(
    connection,
    account
):

    positions = await get_positions(connection)

    if not positions:
        return

    spec = await get_symbol_info(connection)

    point = get_point(spec)
    digits = int(spec.get("digits", 2))

    df = await get_closed_candles(
        account,
        SYMBOL,
        "1m",
        100
    )

    psar, _ = calculate_psar(
        df,
        PSAR_STEP,
        PSAR_MAX
    )

    if psar is None:
        return

    price = await connection.get_symbol_price(
        SYMBOL
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in positions:

        position_id = position.get("id")
        position_type = position.get("type")

        open_price = float(
            position.get("openPrice")
        )

        current_sl = position.get("stopLoss")
        take_profit = position.get("takeProfit")

        # BUY
        if position_type == "POSITION_TYPE_BUY":

            profit_points = (
                bid - open_price
            ) / point

            # PSAR trailing až po +500.
            if profit_points < BE_TRIGGER:
                continue

            candidate_sl = round(
                psar,
                digits
            )

            be_floor = round(
                open_price + BE_LOCK * point,
                digits
            )

            # PSAR nikdy nesmie zhoršiť BE.
            candidate_sl = max(
                candidate_sl,
                be_floor
            )

            if candidate_sl >= bid:
                continue

            if (
                current_sl is not None
                and candidate_sl <= float(current_sl)
            ):
                continue

            await connection.modify_position(
                position_id=position_id,
                stop_loss=candidate_sl,
                take_profit=take_profit
            )

            telegram(
                "📈 M1 PSAR TRAILING BUY\n"
                f"{SYMBOL}\n"
                f"SL → {candidate_sl}"
            )

        # SELL
        elif position_type == "POSITION_TYPE_SELL":

            profit_points = (
                open_price - ask
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            candidate_sl = round(
                psar,
                digits
            )

            be_ceiling = round(
                open_price - BE_LOCK * point,
                digits
            )

            candidate_sl = min(
                candidate_sl,
                be_ceiling
            )

            if candidate_sl <= ask:
                continue

            if (
                current_sl is not None
                and candidate_sl >= float(current_sl)
            ):
                continue

            await connection.modify_position(
                position_id=position_id,
                stop_loss=candidate_sl,
                take_profit=take_profit
            )

            telegram(
                "📉 M1 PSAR TRAILING SELL\n"
                f"{SYMBOL}\n"
                f"SL → {candidate_sl}"
            )


# =========================================================
# JEDNA TRADING SESSION
# =========================================================

async def trading_session(api):

    account = await (
        api.metatrader_account_api
        .get_account(M_ACC)
    )

    if account.state != "DEPLOYED":

        await account.deploy()
        await account.wait_deployed()

    await account.wait_connected()

    connection = account.get_rpc_connection()

    await connection.connect()

    await connection.wait_synchronized()

    telegram(
        "🟢 RIObot SPUSTENÝ – M1 PSAR ONLY\n\n"
        f"Symbol: {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n\n"
        "EMA: VYPNUTÁ\n"
        "5M FILTER: VYPNUTÝ\n"
       
