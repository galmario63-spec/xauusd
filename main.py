import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD
# XAUUSD | M5 | PSAR 2nd DOT
# TP +8.00 | SL -10.00 | BE +5.00 -> +3.00
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

TP_POINTS = 800.0
SL_POINTS = 1000.0

BE_TRIGGER = 500.0
BE_LOCK = 300.0

COMMENT = "RIObot GOLD M5 PSAR 2DOT BE"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# RENDER WEB SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD ACTIVE", 200


def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()


# =========================================================
# TELEGRAM
# =========================================================

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
        print(f"TELEGRAM ERROR: {exc}", flush=True)


# =========================================================
# PSAR
# =========================================================

def psar_values(df):
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()

    count = len(df)
    if count < 3:
        return [None] * count

    psar = [None] * count

    bull = True
    af = PSAR_STEP
    ep = highs[0]
    sar = lows[0]
    psar[0] = sar

    for i in range(1, count):
        sar = sar + af * (ep - sar)

        if bull:
            if i >= 2:
                sar = min(sar, lows[i - 1], lows[i - 2])
            else:
                sar = min(sar, lows[i - 1])

            if lows[i] < sar:
                bull = False
                sar = ep
                ep = lows[i]
                af = PSAR_STEP
            elif highs[i] > ep:
                ep = highs[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

        else:
            if i >= 2:
                sar = max(sar, highs[i - 1], highs[i - 2])
            else:
                sar = max(sar, highs[i - 1])

            if highs[i] > sar:
                bull = True
                sar = ep
                ep = highs[i]
                af = PSAR_STEP
            elif lows[i] < ep:
                ep = lows[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

        psar[i] = sar

    return psar


# =========================================================
# M5 DATA
# =========================================================

async def get_closed_m5(account):
    candles = await asyncio.wait_for(
        account.get_historical_candles(SYMBOL, "5m", None, 120),
        timeout=META_TIMEOUT,
    )

    if not candles or len(candles) < 6:
        return None

    df = pd.DataFrame(candles)

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    # MetaApi môže vrátiť aj aktuálnu otvorenú M5 sviečku.
    if len(df) > 1:
        df = df.iloc[:-1].copy()

    if len(df) < 6:
        return None

    df["psar"] = psar_values(df)
    return df


# =========================================================
# SIGNAL - 2. POTVRDENÁ PSAR BODKA
# =========================================================

def get_signal(df):
    if df is None or len(df) < 4:
        return None

    a = df.iloc[-3]
    b = df.iloc[-2]
    c = df.iloc[-1]

    a_close = float(a["close"])
    b_close = float(b["close"])
    c_close = float(c["close"])

    a_psar = float(a["psar"])
    b_psar = float(b["psar"])
    c_psar = float(c["psar"])

    # BUY: nad cenou -> 1. pod cenou -> 2. pod cenou
    if (
        a_psar > a_close
        and b_psar < b_close
        and c_psar < c_close
    ):
        return "BUY"

    # SELL: pod cenou -> 1. nad cenou -> 2. nad cenou
    if (
        a_psar < a_close
        and b_psar > b_close
        and c_psar > c_close
    ):
        return "SELL"

    return None


# =========================================================
# METAAPI HELPERS
# =========================================================

async def get_positions(connection):
    positions = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT,
    )

    return [
        p
        for p in positions
        if p.get("symbol", "").upper() == SYMBOL
    ]


async def get_market(connection):
    spec = await asyncio.wait_for(
        connection.get_symbol_specification(SYMBOL),
        timeout=META_TIMEOUT,
    )

    price = await asyncio.wait_for(
        connection.get_symbol_price(SYMBOL),
        timeout=META_TIMEOUT,
    )

    digits = int(spec.get("digits", 2))
    point = spec.get("tickSize")

    if not point:
        point = 10 ** (-digits)

    return (
        float(point),
        digits,
        float(price["bid"]),
        float(price["ask"]),
    )


# =========================================================
# OPEN TRADE
# =========================================================

async def open_trade(connection, side):
    point, digits, bid, ask = await get_market(connection)

    if side == "BUY":
        entry = ask
        sl = round(entry - SL_POINTS * point, digits)
        tp = round(entry + TP_POINTS * point, digits)

        result = await asyncio.wait_for(
            connection.create_market_buy_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {"comment": COMMENT},
            ),
            timeout=META_TIMEOUT,
        )

    else:
        entry = bid
        sl = round(entry + SL_POINTS * point, digits)
        tp = round(entry - TP_POINTS * point, digits)

        result = await asyncio.wait_for(
            connection.create_market_sell_order(
                SYMBOL,
                LOT_SIZE,
                sl,
                tp,
                {"comment": COMMENT},
            ),
            timeout=META_TIMEOUT,
        )

    print(
        f"ORDER OK {side} {SYMBOL} "
        f"ENTRY={entry} SL={sl} TP={tp}",
        flush=True,
    )

    telegram(
        "RIObot GOLD\n\n"
        f"{side} {SYMBOL}\n"
        f"Lot: {LOT_SIZE}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        "PSAR: 2nd dot confirmed\n"
        "BE: +5.00 -> +3.00"
    )

    return result


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_be(connection):
    positions = await get_positions(connection)

    if not positions:
        return

    point, digits, bid, ask = await get_market(connection)

    for position in positions:
        position_id = position.get("id")
        side = str(position.get("type", "")).upper()
        entry = float(position.get("openPrice", 0))

        current_sl = position.get("stopLoss")
        current_tp = position.get("takeProfit")

        if current_sl is not None:
            current_sl = float(current_sl)

        if current_tp is not None:
            current_tp = float(current_tp)

        if side in ("BUY", "POSITION_TYPE_BUY"):
            profit_points = (bid - entry) / point
            new_sl = round(entry + BE_LOCK * point, digits)

            if (
                profit_points >= BE_TRIGGER
                and (current_sl is None or current_sl < new_sl)
            ):
                await asyncio.wait_for(
                    connection.modify_position(
                        position_id,
                        new_sl,
                        current_tp,
                    ),
                    timeout=META_TIMEOUT,
                )

                print(f"BE BUY -> {new_sl}", flush=True)

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"BUY {SYMBOL}\n"
                    "+5.00 reached\n"
                    "SL locked +3.00\n"
                    f"SL: {new_sl}"
                )

        elif side in ("SELL", "POSITION_TYPE_SELL"):
            profit_points = (entry - ask) / point
            new_sl = round(entry - BE_LOCK * point, digits)

            if (
                profit_points >= BE_TRIGGER
                and (current_sl is None or current_sl > new_sl)
            ):
                await asyncio.wait_for(
                    connection.modify_position(
                        position_id,
                        new_sl,
                        current_tp,
                    ),
                    timeout=META_TIMEOUT,
                )

                print(f"BE SELL -> {new_sl}", flush=True)

                telegram(
                    "RIObot GOLD BE\n\n"
                    f"SELL {SYMBOL}\n"
                    "+5.00 reached\n"
                    "SL locked +3.00\n"
                    f"SL: {new_sl}"
                )


# =========================================================
# ONE METAAPI SESSION
# =========================================================

async def bot_session(state):
    api = MetaApi(M_TOKEN)
    connection = None

    try:
        account = await asyncio.wait_for(
            api.metatrader_account_api.get_account(M_ACC),
            timeout=META_TIMEOUT,
        )

        # Najprv počká na broker connection.
        try:
            await asyncio.wait_for(
                account.wait_connected(),
                timeout=120,
            )
        except AttributeError:
            # Staršia verzia SDK túto metódu nemusí mať.
            pass

        print("CONNECTING METAAPI...", flush=True)

        connection = account.get_rpc_connection()

        await asyncio.wait_for(
            connection.connect(),
            timeout=60,
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=120,
        )

        print("RIObot GOLD CONNECTED", flush=True)

        # Pri úplne prvom štarte neotvorí starý signál.
        if not state["initialized"]:
            df = await get_closed_m5(account)

            if df is not None:
                state["last_candle"] = str(df.iloc[-1].get("time"))
                state["initialized"] = True

                print(
                    f"STARTUP BASELINE M5={state['last_candle']}",
                    flush=True,
                )

        if not state["ever_connected"]:
            telegram(
                "RIObot GOLD START / CONNECTED\n\n"
                f"Symbol: {SYMBOL}\n"
                f"Lot: {LOT_SIZE}\n"
                "Timeframe: M5\n"
                "Strategy: PSAR 2nd DOT\n"
                "TP: +8.00 price move\n"
                "SL: -10.00 price move\n"
                "BE: +5.00 -> +3.00\n"
                "BTCUSD: OFF"
            )
            state["ever_connected"] = True
        else:
            telegram(
                "RIObot GOLD RECONNECTED\n\n"
                f"{SYMBOL} M5\n"
                "MetaApi connection restored."
            )

        while True:
            await manage_be(connection)

            df = await get_closed_m5(account)

            if df is not None:
                candle = df.iloc[-1]
                candle_time = str(candle.get("time"))

                if candle_time != state["last_candle"]:
                    signal = get_signal(df)

                    print(
                        f"{SYMBOL} M5={candle_time} "
                        f"SIGNAL={signal} PSAR={candle['psar']}",
                        flush=True,
                    )

                    positions = await get_positions(connection)

                    # Označí sviečku ako spracovanú ešte pred order requestom.
                    # Tým zabráni duplicitnému orderu po reconnecte.
                    state["last_candle"] = candle_time

                    if not positions and signal in ("BUY", "SELL"):
                        await open_trade(connection, signal)

            await asyncio.sleep(LOOP_SECONDS)

    finally:
        if connection is not None:
            try:
                await asyncio.wait_for(
                    connection.close(),
                    timeout=10,
                )
            except Exception as exc:
                print(f"CLOSE WARNING: {exc}", flush=True)


# =========================================================
# MAIN / AUTO RECONNECT
# =========================================================

async def main():
    keep_alive()

    if not M_TOKEN:
        raise RuntimeError("M_TOKEN is missing")

    if not M_ACC:
        raise RuntimeError("M_ACC is missing")

    state = {
        "initialized": False,
        "ever_connected": False,
        "last_candle": None,
    }

    while True:
        try:
            await bot_session(state)

        except Exception as exc:
            print(
                f"BOT SESSION ERROR: {type(exc).__name__}: {exc}",
                flush=True,
            )

            print(
                f"RECONNECT IN {RECONNECT_SECONDS} SECONDS...",
                flush=True,
            )

            telegram(
                "RIObot GOLD CONNECTION ERROR\n\n"
                f"Reconnect in {RECONNECT_SECONDS} seconds."
            )

            await asyncio.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
