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

SYMBOL_REQUEST = "BTCUSD"

# CENTOVÝ ÚČET
LOT_SIZE = 0.30

# PARABOLIC SAR
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# SL / TP - 1:1
TP_POINTS = 1500.0
SL_POINTS = 1500.0

# BREAK EVEN
BE_TRIGGER = 500.0
BE_LOCK = 100.0

COMMENT = "Riobot PSAR 5M+1M"
LOOP_SECONDS = 10


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")

T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Riobot is running"


@app.route("/health")
def health():
    return "OK"


def run_server():
    port = int(os.getenv("PORT", "10000"))
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
        print("Telegram chyba:", e)


# =========================================================
# PSAR
# =========================================================

def calculate_psar(df, step=0.02, max_af=0.20):

    if len(df) < 5:
        raise ValueError("Málo sviečok pre PSAR")

    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    psar = [low[0]] * len(df)

    bull = close[1] >= close[0]

    af = step
    ep = high[0] if bull else low[0]

    for i in range(1, len(df)):

        psar[i] = psar[i - 1] + af * (ep - psar[i - 1])

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

            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + step, max_af)

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

            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + step, max_af)

    return float(psar[-1]), bull


# =========================================================
# SYMBOL
# =========================================================

async def get_symbol_info(connection, symbol):

    spec = await connection.get_symbol_specification(symbol)

    if not spec:
        raise Exception(f"Symbol {symbol} nenájdený")

    return spec


# =========================================================
# POZÍCIE
# =========================================================

async def get_positions(connection, symbol):

    positions = await connection.get_positions()

    return [
        p for p in positions
        if p.get("symbol") == symbol
    ]


# =========================================================
# SVIEČKY
# =========================================================

async def get_closed_candles(
    account,
    symbol,
    timeframe,
    limit=100,
    minimum=10
):

    candles = await account.get_historical_candles(
        symbol,
        timeframe,
        None,
        limit
    )

    if not candles or len(candles) < minimum + 1:
        raise Exception(
            f"Málo dát {symbol} {timeframe}"
        )

    df = pd.DataFrame(candles)

    df = df.sort_values("time").reset_index(drop=True)

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    # posledná sviečka sa ešte tvorí
    df = df.iloc[:-1].copy()

    if len(df) < minimum:
        raise Exception(
            f"Málo uzavretých sviečok {timeframe}"
        )

    return df


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_break_even(connection, symbol):

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

    point = float(spec.get("tickSize") or 0.01)
    digits = int(spec.get("digits", 2))

    price = await connection.get_symbol_price(symbol)

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in positions:

        position_id = position["id"]
        side = position["type"]

        open_price = float(
            position["openPrice"]
        )

        current_sl = float(
            position.get("stopLoss") or 0
        )

        tp = position.get("takeProfit")

        if side == "POSITION_TYPE_BUY":

            profit_points = (
                bid - open_price
            ) / point

            new_sl = round(
                open_price + BE_LOCK * point,
                digits
            )

            if (
                profit_points >= BE_TRIGGER
                and (
                    current_sl == 0
                    or new_sl > current_sl
                )
            ):

                await connection.modify_position(
                    position_id,
                    new_sl,
                    tp
                )

                telegram(
                    f"🟢 BREAK EVEN BUY\n"
                    f"{symbol}\n"
                    f"SL → {new_sl}\n"
                    f"Zamknuté: +{BE_LOCK} bodov"
                )

        elif side == "POSITION_TYPE_SELL":

            profit_points = (
                open_price - ask
            ) / point

            new_sl = round(
                open_price - BE_LOCK * point,
                digits
            )

            if (
                profit_points >= BE_TRIGGER
                and (
                    current_sl == 0
                    or new_sl < current_sl
                )
            ):

                await connection.modify_position(
                    position_id,
                    new_sl,
                    tp
                )

                telegram(
                    f"🔴 BREAK EVEN SELL\n"
                    f"{symbol}\n"
                    f"SL → {new_sl}\n"
                    f"Zamknuté: +{BE_LOCK} bodov"
                )


# =========================================================
# PSAR TRAILING - AŽ PO BE
# =========================================================

async def manage_psar_stop(
    connection,
    account,
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

    point = float(spec.get("tickSize") or 0.01)
    digits = int(spec.get("digits", 2))

    min_stop_points = float(
        spec.get("minStopLossDistance") or 0
    )

    min_distance = (
        min_stop_points * point
    )

    df5 = await get_closed_candles(
        account,
        symbol,
        "5m",
        100
    )

    psar5, _ = calculate_psar(
        df5,
        PSAR_STEP,
        PSAR_MAX
    )

    price = await connection.get_symbol_price(
        symbol
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for position in positions:

        position_id = position["id"]
        side = position["type"]

        open_price = float(
            position["openPrice"]
        )

        current_sl = float(
            position.get("stopLoss") or 0
        )

        tp = position.get("takeProfit")

        # ---------------- BUY ----------------

        if side == "POSITION_TYPE_BUY":

            profit_points = (
                bid - open_price
            ) / point

            # trailing nezačne pred BE
            if profit_points < BE_TRIGGER:
                continue

            candidate_sl = round(
                psar5,
                digits
            )

            be_floor = round(
                open_price + BE_LOCK * point,
                digits
            )

            # PSAR nesmie zhoršiť BE
            if candidate_sl < be_floor:
                continue

            if candidate_sl >= bid:
                continue

            if (
                min_distance > 0
                and bid - candidate_sl < min_distance
            ):
                continue

            if (
                current_sl != 0
                and candidate_sl <= current_sl
            ):
                continue

            await connection.modify_position(
                position_id,
                candidate_sl,
                tp
            )

            telegram(
                f"📈 PSAR TRAILING BUY\n"
                f"{symbol}\n"
                f"SL → {candidate_sl}"
            )

        # ---------------- SELL ----------------

        elif side == "POSITION_TYPE_SELL":

            profit_points = (
                open_price - ask
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            candidate_sl = round(
                psar5,
                digits
            )

            be_ceiling = round(
                open_price - BE_LOCK * point,
                digits
            )

            # PSAR nesmie zhoršiť BE
            if candidate_sl > be_ceiling:
                continue

            if candidate_sl <= ask:
                continue

            if (
                min_distance > 0
                and candidate_sl - ask < min_distance
            ):
                continue

            if (
                current_sl != 0
                and candidate_sl >= current_sl
            ):
                continue

            await connection.modify_position(
                position_id,
                candidate_sl,
                tp
            )

            telegram(
                f"📉 PSAR TRAILING SELL\n"
                f"{symbol}\n"
                f"SL → {candidate_sl}"
            )


# =========================================================
# HLAVNÝ ROBOT
# =========================================================

async def trading_session():

    if not M_TOKEN:
        raise Exception("Chýba M_TOKEN")

    if not M_ACC:
        raise Exception("Chýba M_ACC")

    api = MetaApi(M_TOKEN)

    account = await api.metatrader_account_api.get_account(
        M_ACC
    )

    if account.state != "DEPLOYED":
        await account.deploy()
        await account.wait_deployed()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    symbol = SYMBOL_REQUEST

    telegram(
        f"🟢 RIObot spustený – PSAR ONLY\n\n"
        f"Symbol: {symbol}\n"
        f"Lot: {LOT_SIZE}\n\n"
        f"Smer: 5M PSAR\n"
        f"Vstup: 1M PSAR FLIP\n"
        f"EMA FILTER: VYPNUTÝ\n\n"
        f"TP: {TP_POINTS} bodov\n"
        f"SL: {SL_POINTS} bodov\n"
        f"BE: +{BE_TRIGGER} → +{BE_LOCK}\n\n"
        f"PSAR trailing: až po BE\n"
        f"Max. 1 otvorená pozícia"
    )

    last_processed_m1 = None

    while True:

        try:

            # BE ide prvý
            await manage_break_even(
                connection,
                symbol
            )

            # potom PSAR trailing
            await manage_psar_stop(
                connection,
                account,
                symbol
            )

            # ---------------------------------------------
            # 5M PSAR = SMER
            # ---------------------------------------------

            df5 = await get_closed_candles(
                account,
                symbol,
                "5m",
                100
            )

            psar5, bull5 = calculate_psar(
                df5,
                PSAR_STEP,
                PSAR_MAX
            )

            # ---------------------------------------------
            # 1M PSAR = VSTUP
            # ---------------------------------------------

            df1 = await get_closed_candles(
                account,
                symbol,
                "1m",
                100
            )

            if len(df1) < 10:
                await asyncio.sleep(LOOP_SECONDS)
                continue

            previous_df = df1.iloc[:-1].copy()

            previous_psar, previous_bull = calculate_psar(
                previous_df,
                PSAR_STEP,
                PSAR_MAX
            )

            current_psar, current_bull = calculate_psar(
                df1,
                PSAR_STEP,
                PSAR_MAX
            )

            latest_close = float(
                df1.iloc[-1]["close"]
            )

            m1_candle_id = str(
                df1.iloc[-1]["time"]
            )

            signal = None

            # BUY:
            # 5M PSAR bullish
            # 1M práve prehodil SELL -> BUY

            if (
                bull5
                and not previous_bull
                and current_bull
            ):
                signal = "BUY"

            # SELL:
            # 5M PSAR bearish
            # 1M práve prehodil BUY -> SELL

            elif (
                not bull5
                and previous_bull
                and not current_bull
            ):
                signal = "SELL"

            # ---------------------------------------------
            # MAX 1 POZÍCIA
            # ---------------------------------------------

            positions = await get_positions(
                connection,
                symbol
            )

            if positions:
                await asyncio.sleep(
                    LOOP_SECONDS
                )
                continue

            if signal is None:
                await asyncio.sleep(
                    LOOP_SECONDS
                )
                continue

            # rovnaký flip už druhýkrát neobchodovať

            if last_processed_m1 == m1_candle_id:
                await asyncio.sleep(
                    LOOP_SECONDS
                )
                continue

            last_processed_m1 = m1_candle_id

            # ---------------------------------------------
            # SYMBOL INFO
            # ---------------------------------------------

            spec = await get_symbol_info(
                connection,
                symbol
            )

            point = float(
                spec.get("tickSize") or 0.01
            )

            digits = int(
                spec.get("digits", 2)
            )

            # ---------------------------------------------
            # CENY
            # ---------------------------------------------

            async def get_order_prices():

                price_data = await connection.get_symbol_price(
                    symbol
