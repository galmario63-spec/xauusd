import asyncio
import os
from threading import Thread
import pandas as pd
import requests
from flask import Flask
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

COMMENT = "Riobot 5M+1M PSAR"

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot running"


def keep_alive():
    Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", "10000"))
        ),
        daemon=True
    ).start()


def telegram(msg):
    if T_TOKEN and T_CHAT:
        try:
            requests.post(
                f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
                data={"chat_id": T_CHAT, "text": msg},
                timeout=10
            )
        except Exception as e:
            print("Telegram:", e)


def psar(df):
    h = df.high.astype(float).tolist()
    l = df.low.astype(float).tolist()
    c = df.close.astype(float).tolist()

    if len(df) < 5:
        raise ValueError("Malo dat pre PSAR")

    p = [0.0] * len(df)
    bull = c[1] >= c[0]
    af = PSAR_STEP

    ep = h[0] if bull else l[0]
    p[0] = l[0] if bull else h[0]

    for i in range(1, len(df)):
        p[i] = p[i - 1] + af * (ep - p[i - 1])

        if bull:
            p[i] = (
                min(p[i], l[i - 1], l[i - 2])
                if i >= 2
                else min(p[i], l[i - 1])
            )

            if l[i] < p[i]:
                bull = False
                p[i] = ep
                ep = l[i]
                af = PSAR_STEP

            elif h[i] > ep:
                ep = h[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

        else:
            p[i] = (
                max(p[i], h[i - 1], h[i - 2])
                if i >= 2
                else max(p[i], h[i - 1])
            )

            if h[i] > p[i]:
                bull = True
                p[i] = ep
                ep = h[i]
                af = PSAR_STEP

            elif l[i] < ep:
                ep = l[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

    return p[-1], bull


async def candles(account, tf):
    data = await account.get_historical_candles(
        SYMBOL, tf, None, 100
    )

    if not data or len(data) < 11:
        raise RuntimeError("Malo dat " + tf)

    df = pd.DataFrame(data)
    df = df.sort_values("time").reset_index(drop=True)

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    # iba uzavrete sviecky
    return df.iloc[:-1].copy()


async def positions(conn):
    all_pos = await conn.get_positions()

    return [
        p for p in all_pos
        if p.get("symbol") == SYMBOL
    ]


async def spec(conn):
    s = await conn.get_symbol_specification(SYMBOL)

    if not s:
        raise RuntimeError("Symbol nenajdeny")

    point = float(s.get("tickSize") or 0.01)
    digits = int(s.get("digits", 2))

    return point, digits


async def protect(conn):
    pos = await positions(conn)

    if not pos:
        return

    point, digits = await spec(conn)
    price = await conn.get_symbol_price(SYMBOL)

    bid = float(price["bid"])
    ask = float(price["ask"])

    for p in pos:
        op = float(p["openPrice"])
        old_sl = float(p.get("stopLoss") or 0)
        tp = p.get("takeProfit")

        if p["type"] == "POSITION_TYPE_BUY":
            profit = (bid - op) / point

            if profit >= BE_TRIGGER:
                new_sl = round(
                    op + BE_LOCK * point,
                    digits
                )

                if old_sl == 0 or new_sl > old_sl:
                    await conn.modify_position(
                        p["id"],
                        new_sl,
                        tp
                    )

        elif p["type"] == "POSITION_TYPE_SELL":
            profit = (op - ask) / point

            if profit >= BE_TRIGGER:
                new_sl = round(
                    op - BE_LOCK * point,
                    digits
                )

                if old_sl == 0 or new_sl < old_sl:
                    await conn.modify_position(
                        p["id"],
                        new_sl,
                        tp
                    )


async def order(conn, side, psar_value):
    point, digits = await spec(conn)
    price = await conn.get_symbol_price(SYMBOL)

    ask = float(price["ask"])
    bid = float(price["bid"])

    entry = ask if side == "BUY" else bid

    if side == "BUY":
        sl = round(
            entry - SL_POINTS * point,
            digits
        )
        tp = round(
            entry + TP_POINTS * point,
            digits
        )
    else:
        sl = round(
            entry + SL_POINTS * point,
            digits
        )
        tp = round(
            entry - TP_POINTS * point,
            digits
        )

    options = {"comment": COMMENT}

    if side == "BUY":
        result = await conn.create_market_buy_order(
            SYMBOL,
            LOT_SIZE,
            sl,
            tp,
            options
        )
    else:
        result = await conn.create_market_sell_order(
            SYMBOL,
            LOT_SIZE,
            sl,
            tp,
            options
        )

    telegram(
        f"{side} {SYMBOL} otvoreny\n"
        f"Entry {entry}\n"
        f"SL {sl}\n"
        f"TP {tp}\n"
        f"PSAR {psar_value:.2f}"
    )

    print("ORDER:", result)


async def session(api):
    account = await (
        api.metatrader_account_api.get_account(
            M_ACC
        )
    )

    if account.state != "DEPLOYED":
        await account.deploy()
        await account.wait_deployed()

    conn = account.get_rpc_connection()

    print("Pripajam MetaApi...")

    await conn.connect()
    await conn.wait_synchronized()

    print("MetaApi synchronizovane")

    telegram(
        f"RIObot START\n"
        f"{SYMBOL} lot {LOT_SIZE}\n"
        f"5M PSAR smer + M1 PSAR flip"
    )

    last_candle = None

    try:
        while True:
            await protect(conn)

            # 5M urcuje smer
            df5 = await candles(account, "5m")
            _, bull5 = psar(df5)

            # 1M urcuje vstup
            df1 = await candles(account, "1m")

            _, previous_bull = psar(
                df1.iloc[:-1]
            )

            current_psar, current_bull = psar(
                df1
            )

            signal = None

            if (
                bull5
                and not previous_bull
                and current_bull
            ):
                signal = "BUY"

            elif (
                not bull5
                and previous_bull
                and not current_bull
            ):
                signal = "SELL"

            candle_id = str(
                df1.iloc[-1]["time"]
            )

            print(
                SYMBOL,
                "5M:",
                bull5,
                "1M:",
                current_bull,
                "SIGNAL:",
                signal
            )

            open_positions = await positions(conn)

            if (
                signal
               
