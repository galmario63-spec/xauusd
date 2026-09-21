import asyncio
import os
from flask import Flask
from threading import Thread
import pandas as pd
import requests
from metaapi_cloud_sdk import MetaApi

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

COMMENT = "Riobot M1 PSAR"

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot running"


def run_server():
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000"))
    )


def keep_alive():
    Thread(
        target=run_server,
        daemon=True
    ).start()


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
        print("Telegram error:", e)


def calculate_psar(df):
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    if len(df) < 5:
        raise ValueError("Malo dat pre PSAR")

    psar = [0.0] * len(df)

    bull = close[1] >= close[0]
    af = PSAR_STEP

    if bull:
        ep = high[0]
        psar[0] = low[0]
    else:
        ep = low[0]
        psar[0] = high[0]

    for i in range(1, len(df)):
        psar[i] = (
            psar[i - 1]
            + af * (ep - psar[i - 1])
        )

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
                af = PSAR_STEP

            elif high[i] > ep:
                ep = high[i]
                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

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
                af = PSAR_STEP

            elif low[i] < ep:
                ep = low[i]
                af = min(
                    af + PSAR_STEP,
                    PSAR_MAX
                )

    return float(psar[-1]), bull


async def candles(account):
    data = await account.get_historical_candles(
        SYMBOL,
        "1m",
        None,
        100
    )

    if not data or len(data) < 11:
        raise Exception("Malo M1 dat")

    df = pd.DataFrame(data)

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    # odstranime aktualnu neuzavretu sviecku
    df = df.iloc[:-1].copy()

    if len(df) < 10:
        raise Exception(
            "Malo uzavretych M1 sviecok"
        )

    return df


async def symbol_info(connection):
    spec = await connection.get_symbol_specification(
        SYMBOL
    )

    if not spec:
        raise Exception("BTCUSD nenajdeny")

    return spec


def point_from_spec(spec):
    return float(
        spec.get("tickSize") or 0.01
    )


async def positions(connection):
    all_positions = await connection.get_positions()

    return [
        p for p in all_positions
        if p.get("symbol") == SYMBOL
    ]


async def protect_position(connection):
    pos = await positions(connection)

    if not pos:
        return

    spec = await symbol_info(connection)

    point = point_from_spec(spec)
    digits = int(spec.get("digits", 2))

    price = await connection.get_symbol_price(
        SYMBOL
    )

    bid = float(price["bid"])
    ask = float(price["ask"])

    for p in pos:
        side = p["type"]

        open_price = float(
            p["openPrice"]
        )

        old_sl = float(
            p.get("stopLoss") or 0
        )

        tp = p.get("takeProfit")

        if side == "POSITION_TYPE_BUY":
            profit = (
                bid - open_price
            ) / point

            new_sl = round(
                open_price + BE_LOCK * point,
                digits
            )

            if (
                profit >= BE_TRIGGER
                and (
                    old_sl == 0
                    or new_sl > old_sl
                )
            ):
                await connection.modify_position(
                    p["id"],
                    new_sl,
                    tp
                )

                telegram(
                    f"BE BUY {SYMBOL}: "
                    f"SL -> {new_sl}"
                )

        elif side == "POSITION_TYPE_SELL":
            profit = (
                open_price - ask
            ) / point

            new_sl = round(
                open_price - BE_LOCK * point,
                digits
            )

            if (
                profit >= BE_TRIGGER
                and (
                    old_sl == 0
                    or new_sl < old_sl
                )
            ):
                await connection.modify_position(
                    p["id"],
                    new_sl,
                    tp
                )

                telegram(
                    f"BE SELL {SYMBOL}: "
                    f"SL -> {new_sl}"
                )


async def open_trade(
    connection,
    signal,
    psar
):
    spec = await symbol_info(connection)

    point = point_from_spec(spec)
    digits = int(spec.get("digits", 2))

    for attempt in range(2):
        price = await connection.get_symbol_price(
            SYMBOL
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
            options = {
                "comment": COMMENT
            }

            if signal == "BUY":
                result = await (
                    connection.create_market_buy_order(
                        SYMBOL,
                        LOT_SIZE,
                        sl,
                        tp,
                        options
                    )
                )

            else:
                result = await (
                    connection.create_market_sell_order(
                        SYMBOL,
                        LOT_SIZE,
                        sl,
                        tp,
                        options
                    )
                )

            print(
                signal,
                "OPENED",
                result
            )

            telegram(
                f"{signal} OTVORENY\n"
                f"{SYMBOL}\n"
                f"Lot: {LOT_SIZE}\n"
                f"Entry: {entry}\n"
                f"SL: {sl}\n"
                f"TP: {tp}\n"
                f"PSAR: {psar:.2f}"
            )

            return True

        except Exception as e:
            print(
                "ORDER ERROR:",
                e
            )

            if (
                attempt == 0
                and "Invalid stops" in str(e)
            ):
                await asyncio.sleep(1)
                continue

            raise

    return False


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

    print("Pripajam RPC...")

    await connection.connect()

    print("Cakam na synchronizaciu...")

    await connection.wait_synchronized()

    print("MetaApi synchronizovane")

    telegram(
        "RIObot SPUSTENY\n"
        "M1 PSAR ONLY\n"
