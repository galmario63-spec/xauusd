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

# CENTOVÝ ÚČET
LOT_SIZE = 0.30

# PARABOLIC SAR
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# TP / SL 1:1
TP_POINTS = 1500.0
SL_POINTS = 1500.0

# BREAK EVEN
BE_TRIGGER = 500.0
BE_LOCK = 100.0

COMMENT = "Riobot M1 PSAR ONLY"

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
# PARABOLIC SAR
# =========================================================

def calculate_psar(df, step=0.02, max_af=0.20):

    if len(df) < 5:
        raise ValueError("Málo sviečok pre PSAR")

    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    psar = [0.0] * len(df)

    bull = close[1] >= close[0]
    af = step

    if bull:
        ep = high[0]
        psar[0] = low[0]
    else:
        ep = low[0]
        psar[0] = high[0]

    for i in range(1, len(df)):

        psar[i] = psar[i - 1] + af * (
            ep - psar[i - 1]
        )

        # BULL
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
                    af = min(
                        af + step,
                        max_af
                    )

        # BEAR
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
        symbol,
        timeframe,
        None,
        limit
    )

    if not candles:
        raise Exception(
            f"Žiadne dáta {symbol} {timeframe}"
        )

    if len(candles) < 11:
        raise Exception(
            f"Málo dát {symbol} {timeframe}"
        )

    df = pd.DataFrame(candles)

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    # odstráni práve tvoriacu sa sviečku
    df = df.iloc[:-1].copy()

    if len(df) < 10:
        raise Exception(
            "Málo uzavretých M1 sviečok"
        )

    return df


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(connection, symbol):

    spec = await connection.get_symbol_specification(
        symbol
    )

    if not spec:
        raise Exception(
            f"Symbol {symbol} nenájdený"
        )

    return spec


def get_point(spec):
    return float(
        spec.get("tickSize") or 0.01
    )


# =========================================================
# POZÍCIE
# =========================================================

async def get_positions(connection, symbol):

    positions = await connection.get_positions()

    return [
        position
        for position in positions
        if position.get("symbol") == symbol
    ]


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

    point = get_point(spec)
    digits = int(spec.get("digits", 2))

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

        # BUY
        if side == "POSITION_TYPE_BUY":

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
                current_sl != 0
                and current_sl >= new_sl
            ):
                continue

            await connection.modify_position(
                position_id,
                new_sl,
                tp
            )

            telegram(
                f"🟢 BREAK EVEN BUY\n"
                f"{symbol}\n"
                f"SL → {new_sl}\n"
                f"Zamknuté +{BE_LOCK:.0f} bodov"
            )

        # SELL
        elif side == "POSITION_TYPE_SELL":

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
                current_sl != 0
                and current_sl <= new_sl
            ):
                continue

            await connection.modify_position(
                position_id,
                new_sl,
                tp
            )

            telegram(
                f"🔴 BREAK EVEN SELL\n"
                f"{symbol}\n"
                f"SL → {new_sl}\n"
                f"Zamknuté +{BE_LOCK:.0f} bodov"
            )


# =========================================================
# PSAR TRAILING M1
# =========================================================

async def manage_psar_trailing(
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

    point = get_point(spec)
    digits = int(spec.get("digits", 2))

    df = await get_closed_candles(
        account,
        symbol,
        "1m",
        100
    )

    psar, _ = calculate_psar(
        df,
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

        # BUY
        if side == "POSITION_TYPE_BUY":

            profit_points = (
                bid - open_price
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            be_floor = round(
                open_price + BE_LOCK * point,
                digits
            )

            candidate_sl = round(
                max(psar, be_floor),
                digits
            )

            if candidate_sl >= bid:
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

        # SELL
        elif side == "POSITION_TYPE_SELL":

            profit_points = (
                open_price - ask
            ) / point

            if profit_points < BE_TRIGGER:
                continue

            be_ceiling = round(
                open_price - BE_LOCK * point,
                digits
            )

            candidate_sl = round(
                min(psar, be_ceiling),
                digits
            )

            if candidate_sl <= ask:
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
# OTVORENIE OBCHODU
# =========================================================

async def open_trade(
    connection,
    signal,
    symbol,
    psar
):

    spec = await get_symbol_info(
        connection,
        symbol
    )

    point = get_point(spec)
    digits = int(spec.get("digits", 2))

    for attempt in range(2):

        price = await connection.get_symbol_price(
            symbol
        )

        ask = float(price["ask"])
        bid = float(price["bid"])

        if signal == "BUY":

            entry = ask

            sl = round(
                entry - SL_POINTS * point,
                digits
            )

            tp = round(
                entry + TP_POINTS * point,
                digits
            )

        else:

            entry = bid

            sl = round(
                entry + SL_POINTS * point,
                digits
            )

            tp = round(
                entry - TP_POINTS * point,
                digits
            )

        try:

            if signal == "BUY":

                result = await connection.create_market_buy_order(
                    symbol,
                    LOT_SIZE,
                    sl,
                    tp,
                    {
                        "comment": COMMENT
                    }
                )

            else:

                result = await connection.create_market_sell_order(
                    symbol,
                    LOT_SIZE,
                    sl,
                    tp,
                    {
                        "comment": COMMENT
                    }
                )

            print(
                signal,
                "OPENED:",
                result
            )

            telegram(
                f"{'🟢 BUY' if signal == 'BUY' else '🔴 SELL'} OTVORENÝ\n\n"
                f"{symbol}\n"
                f"Lot: {LOT_SIZE}\n"
                f"Cena: {entry}\n"
                f"SL: {sl}\n"
                f"TP: {tp}\n"
                f"PSAR: {psar:.2f}\n\n"
                f"BE +{BE_TRIGGER:.0f} → +{BE_LOCK:.0f}"
            )

            return True

        except Exception as e:

            error_text = str(e)

            print(
                "ORDER ERROR:",
                error_text
            )

            if (
                attempt == 0
                and "Invalid stops" in error_text
            ):
                await asyncio.sleep(1)
                continue

            raise

    return False


# =========================================================
# TRADING SESSION
# =========================================================

async def trading_session(api):

    account = await (
        api.metatrader_account_api.get_account(
            M_ACC
        )
    )

    if account.state != "DEPLOYED":

        await account.deploy()
        await account.wait_deployed()

    connection = account.get_rpc_connection()

    print("Pripájam RPC...")

    await connection.connect()

    print("Čakám na synchronizáciu...")

    await connection.wait_synchronized()

    print("MetaApi synchronizované.")

    telegram(
        "🟢 RIObot SPUSTENÝ\n\n"
        "M1 PSAR ONLY\n"
        f"Symbol: {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n\n"
        "EMA: OFF\n"
        "5M FILTER: OFF\n"
        "Vstup: M1 PSAR FLIP\n\n"
        f"TP: {TP_POINTS:.0f}\n"
        f"SL: {SL_POINTS:.0f}\n"
        f"BE: +{BE_TRIGGER:.0f} → +{BE_LOCK:.0f}\n"
        "AUTO RECONNECT: ON"
    )

    last_processed_m1 = None
    connection_errors = 0

    try:

        while True:

            try:

                # OCHRANA OTVORENEJ POZÍCIE
                await manage_break_even(
                    connection,
                    SYMBOL
                )

                await manage_psar_trailing(
                    connection,
                    account,
                    SYMBOL
                )

                # M1 DÁTA
                df = await get_closed_candles(
                    account,
                    SYMBOL,
                    "1m",
                    100
                )

                previous_df = df.iloc[:-1].copy()

                previous_psar, previous_bull = (
                    calculate_psar(
                        previous_df,
                        PSAR_STEP,
                        PSAR_MAX
                    )
                )

                current_psar, current_bull = (
                    calculate_psar(
                        df,
                        PSAR_STEP,
                        PSAR_MAX
                    )
                )

                connection_errors = 0

                # PSAR FLIP
                buy_flip = (
                    previous_bull is False
                    and current_bull is True
                )

                sell_flip = (
                    previous_bull is True
                    and current_bull is False
                )

                signal = None

                if buy_flip:
                    signal = "BUY"

                elif sell_flip:
                    signal = "SELL"

                candle_id = str(
                    df.iloc[-1]["time"]
                )

                print(
                    f"{SYMBOL} | "
                    f"PSAR={current_psar:.2f} | "
                    f"BULL={current_bull} | "
                    f"BUY_FLIP={buy_flip} | "
                    f"SELL_FLIP={sell_flip} | "
                    f"SIGNAL={signal}"
                )

                # MAX 1 POZÍCIA
                positions = await get_positions(
                    connection,
                    SYMBOL
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

                # rovnakú uzavretú M
