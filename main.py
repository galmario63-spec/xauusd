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

TP_POINTS = 3000.0
SL_POINTS = 3000.0
BE_TRIGGER = 1000.0
BE_LOCK = 200.0

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
COMMENT = "Riobot M1 PSAR ONLY"

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot M1 PSAR active"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()


def telegram(message):
    if not T_TOKEN or not T_CHAT:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={"chat_id": T_CHAT, "text": message},
            timeout=10,
        )
    except Exception as exc:
        print("Telegram error:", exc, flush=True)


def psar_values(df):
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()
    close = df["close"].astype(float).tolist()

    n = len(df)

    if n < 5:
        raise ValueError("Not enough candles for PSAR")

    psar = [0.0] * n

    bull = close[1] >= close[0]
    af = PSAR_STEP

    if bull:
        psar[0] = low[0]
        ep = high[0]
    else:
        psar[0] = high[0]
        ep = low[0]

    states = [bull]

    for i in range(1, n):

        value = psar[i - 1] + af * (ep - psar[i - 1])

        if bull:

            value = min(value, low[i - 1])

            if i > 1:
                value = min(value, low[i - 2])

            if low[i] < value:

                bull = False
                value = ep
                ep = low[i]
                af = PSAR_STEP

            else:

                if high[i] > ep:
                    ep = high[i]
                    af = min(af + PSAR_STEP, PSAR_MAX)

        else:

            value = max(value, high[i - 1])

            if i > 1:
                value = max(value, high[i - 2])

            if high[i] > value:

                bull = True
                value = ep
                ep = high[i]
                af = PSAR_STEP

            else:

                if low[i] < ep:
                    ep = low[i]
                    af = min(af + PSAR_STEP, PSAR_MAX)

        psar[i] = value
        states.append(bull)

    return psar, states


async def get_closed_m1(account):

    candles = await account.get_historical_candles(
        SYMBOL,
        "1m",
        None,
        100,
    )

    if not candles or len(candles) < 10:
        raise RuntimeError("Not enough M1 candles")

    df = pd.DataFrame(candles)

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    if len(df) < 6:
        raise RuntimeError("Not enough valid M1 candles")

    # nepouzivame prave tvoriacu sa sviecku
    return df.iloc[:-1].reset_index(drop=True)


def signal_from_psar(df):

    psar, states = psar_values(df)

    if len(states) < 2:
        return None, psar[-1]

    # PSAR flip na BUY
    if (not states[-2]) and states[-1]:
        return "BUY", psar[-1]

    # PSAR flip na SELL
    if states[-2] and (not states[-1]):
        return "SELL", psar[-1]

    return None, psar[-1]


async def symbol_info(connection):

    spec = await connection.get_symbol_specification(SYMBOL)

    digits = int(
        spec.get("digits", 2)
    )

    point = float(
        spec.get("tickSize") or
        (10 ** (-digits))
    )

    return digits, point


async def open_trade(connection, side, psar):

    digits, point = await symbol_info(connection)

    for attempt in range(2):

        price_data = await connection.get_symbol_price(
            SYMBOL
        )

        if side == "BUY":

            entry = float(price_data["ask"])

            sl = round(
                entry - SL_POINTS * point,
                digits,
            )

            tp = round(
                entry + TP_POINTS * point,
                digits,
            )

        else:

            entry = float(price_data["bid"])

            sl = round(
                entry + SL_POINTS * point,
                digits,
            )

            tp = round(
                entry - TP_POINTS * point,
                digits,
            )

        try:

            if side == "BUY":

                await connection.create_market_buy_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    {"comment": COMMENT},
                )

            else:

                await connection.create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    tp,
                    {"comment": COMMENT},
                )

            telegram(
                f"{side} {SYMBOL}\n"
                f"Entry: {entry:.2f}\n"
                f"SL: {sl:.2f}\n"
                f"TP: {tp:.2f}\n"
                f"M1 PSAR: {psar:.2f}"
            )

            print(
                side,
                "opened",
                entry,
                sl,
                tp,
                flush=True,
            )

            return True

        except Exception as exc:

            print(
                "Order error:",
                exc,
                flush=True,
            )

            if (
                attempt == 0
                and "invalid stops" in str(exc).lower()
            ):
                await asyncio.sleep(1)
                continue

            return False

    return False


async def manage_position(
    connection,
    position,
    psar,
):

    try:

        digits, point = await symbol_info(
            connection
        )

        price_data = await connection.get_symbol_price(
            SYMBOL
        )

        position_id = position["id"]
        position_type = position["type"]

        entry = float(
            position["openPrice"]
        )

        current_sl = float(
            position.get("stopLoss") or 0
        )

        current_tp = position.get(
            "takeProfit"
        )

        tp = (
            float(current_tp)
            if current_tp is not None
            else None
        )

        # BUY
        if position_type == "POSITION_TYPE_BUY":

            current = float(
                price_data["bid"]
            )

            profit_points = (
                current - entry
            ) / point

            if profit_points < BE_TRIGGER:
                return

            be_sl = round(
                entry + BE_LOCK * point,
                digits,
            )

            new_sl = be_sl

            # PSAR trailing az po aktivacii BE
            if psar < current:

                new_sl = max(
                    new_sl,
                    round(psar, digits),
                )

            if (
                new_sl > current_sl
                and new_sl < current
            ):

                await connection.modify_position(
                    position_id,
                    new_sl,
                    tp,
                )

                print(
                    "BUY SL moved:",
                    new_sl,
                    flush=True,
                )

        # SELL
        elif position_type == "POSITION_TYPE_SELL":

            current = float(
                price_data["ask"]
            )

            profit_points = (
                entry - current
            ) / point

            if profit_points < BE_TRIGGER:
                return

            be_sl = round(
                entry - BE_LOCK * point,
                digits,
            )

            new_sl = be_sl

            # PSAR trailing az po aktivacii BE
            if psar > current:

                new_sl = min(
                    new_sl,
                    round(psar, digits),
                )

            if (
                (current_sl == 0 or new_sl < current_sl)
                and new_sl > current
            ):

                await connection.modify_position(
                    position_id,
                    new_sl,
                    tp,
                )

                print(
                    "SELL SL moved:",
                    new_sl,
                    flush=True,
                )

    except Exception as exc:

        print(
            "BE/trailing error:",
            exc,
            flush=True,
        )


async def trading_session(api):

    account = await api.metatrader_account_api.get_account(
        M_ACC
    )

    if account.state != "DEPLOYED":

        await account.deploy()
        await account.wait_deployed()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    telegram(
        "RIObot START\n"
        f"{SYMBOL} lot {LOT_SIZE}\n"
        "M1 PSAR ONLY\n"
        "EMA OFF | 5M FILTER OFF\n"
        f"TP {TP_POINTS} | SL {SL_POINTS}\n"
        f"BE +{BE_TRIGGER} -> +{BE_LOCK}"
    )

    last_candle = None

    try:

        while True:

            df = await get_closed_m1(
                account
            )

            signal, psar = signal_from_psar(
                df
            )

            candle_id = str(
                df.iloc[-1].get(
                    "time",
                    len(df),
                )
            )

            positions = await connection.get_positions()

            positions = [
                p
                for p in positions
                if p.get("symbol") == SYMBOL
            ]

            # ak uz mame obchod, iba ho spravujeme
            if positions:

                await manage_position(
                    connection,
                    positions[0],
                    psar,
                )

            # novy obchod iba raz na uzavretu M1 sviecku
            elif candle_id != last_candle:

                last_candle = candle_id

                if signal:

                    await open_trade(
                        connection,
                        signal,
                        psar,
                    )

            await asyncio.sleep(
                LOOP_SECONDS
            )

    finally:

        try:
            await connection.close()

        except Exception:
            pass


async def main():

    if not M_TOKEN or not M_ACC:
        raise RuntimeError(
            "Missing M_TOKEN or M_ACC"
        )

    keep_alive()

    api = MetaApi(M_TOKEN)

    while True:

        try:

            await trading_session(
                api
            )

        except Exception as exc:

            print(
                "BOT ERROR:",
                exc,
                flush=True,
            )

            telegram(
                f"RIObot ERROR: {exc}"
            )

            print(
                f"Reconnect in {RECONNECT_SECONDS}s",
                flush=True,
            )

            await asyncio.sleep(
                RECONNECT_SECONDS
            )


if __name__ == "__main__":
    asyncio.run(main())
