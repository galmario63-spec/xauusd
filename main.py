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

# PSAR
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# SL / TP V BODOCH
TP_POINTS = 600.0
SL_POINTS = 1500.0

MAGIC = 26092026
COMMENT = "Riobot PSAR"

# BREAK EVEN
BE_TRIGGER = 250.0
BE_LOCK = 100.0

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


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):

    if not T_TOKEN or not T_CHAT:
        print(message)
        return

    try:

        url = f"https://api.telegram.org/bot{T_TOKEN}/sendMessage"

        requests.post(
            url,
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
# ERROR
# =========================================================

def get_error_details(api, error):

    try:
        return api.format_error(error)

    except Exception:

        return str(error)


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(connection, symbol):

    try:

        return await connection.get_symbol_specification(
            symbol
        )

    except Exception as e:

        print(
            "Symbol specification chyba:",
            e
        )

        return None


# =========================================================
# POZÍCIE
# =========================================================

async def get_positions(connection, symbol):

    try:

        positions = await connection.get_positions()

        return [
            p for p in positions
            if p.get("symbol") == symbol
        ]

    except Exception as e:

        print(
            "Chyba pri načítaní pozícií:",
            e
        )

        return []


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_break_even(connection, symbol):

    try:

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

            try:

                position_id = position.get("id")
                position_type = position.get("type")

                open_price = float(
                    position.get("openPrice")
                )

                current_price = float(
                    position.get("currentPrice")
                )

                current_sl = position.get(
                    "stopLoss"
                )

                take_profit = position.get(
                    "takeProfit"
                )

                # =========================================
                # BUY BE
                # =========================================

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

                            await connection.modify_position(
                                position_id=position_id,
                                stop_loss=new_sl,
                                take_profit=take_profit
                            )

                            telegram(
                                "🟢 BREAK EVEN – BUY\n\n"
                                f"Symbol: {symbol}\n"
                                f"Open: {open_price}\n"
                                f"Nový SL: {new_sl}\n"
                                f"Profit: "
                                f"{profit_points:.0f} bodov"
                            )

                # =========================================
                # SELL BE
                # =========================================

                elif position_type == "POSITION_TYPE_SELL":

                    profit_points = (
                        open_price - current_price
                    ) / point

                    if profit_points >= BE_TRIGGER:

                        new_sl = round(
                            open_price
                            - BE_LOCK * point,
                            digits
                        )

                        if (
                            current_sl is None
                            or float(current_sl) > new_sl
                        ):

                            await connection.modify_position(
                                position_id=position_id,
                                stop_loss=new_sl,
                                take_profit=take_profit
                            )

                            telegram(
                                "🔴 BREAK EVEN – SELL\n\n"
                                f"Symbol: {symbol}\n"
                                f"Open: {open_price}\n"
                                f"Nový SL: {new_sl}\n"
                                f"Profit: "
                                f"{profit_points:.0f} bodov"
                            )

            except Exception:

                print(
                    "BE chyba:",
                    traceback.format_exc()
                )

    except Exception:

        print(
            "BE systém chyba:",
            traceback.format_exc()
        )


# =========================================================
# HLAVNÝ BOT
# =========================================================

async def main():

    if not M_TOKEN:

        telegram(
            "❌ Chýba M_TOKEN"
        )

        return

    if not M_ACC:

        telegram(
            "❌ Chýba M_ACC"
        )

        return

    api = MetaApi(M_TOKEN)

    connection = None

    try:

        # =============================================
        # METAAPI
        # =============================================

        account = await (
            api.metatrader_account_api
            .get_account(M_ACC)
        )

        await account.wait_connected()

        connection = account.get_rpc_connection()

        await connection.connect()

        await connection.wait_synchronized()

        telegram(
            "🟢 RIObot spustený\n\n"
            f"Symbol: {SYMBOL_REQUEST}\n"
            f"Lot: {LOT_SIZE}\n"
            f"TP: {TP_POINTS} bodov\n"
            f"SL: {SL_POINTS} bodov\n"
            f"BE trigger: {BE_TRIGGER} bodov\n"
            f"BE lock: {BE_LOCK} bodov\n\n"
            "PSAR: ON\n"
            "clientId: VYPNUTÝ"
        )

        last_signal = None

        # =============================================
        # HLAVNÝ LOOP
        # =============================================

        while True:

            try:

                symbol = SYMBOL_REQUEST

                # =====================================
                # BREAK EVEN
                # =====================================

                await manage_break_even(
                    connection,
                    symbol
                )

                # =====================================
                # HISTORICKÉ CANDLES
                # =====================================

                candles = await (
                    account.get_historical_candles(
                        symbol=symbol,
                        timeframe="1m",
                        start_time=None,
                        limit=50
                    )
                )

                if not candles:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                df = pd.DataFrame(candles)

                if df.empty:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

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

                    print(
                        "Chýbajú OHLC dáta:",
                        df.columns.tolist()
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                for col in required:

                    df[col] = pd.to_numeric(
                        df[col],
                        errors="coerce"
                    )

                df = df.dropna(
                    subset=required
                )

                if len(df) < 10:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =====================================
                # PSAR
                # =====================================

                psar, bullish = calculate_psar(
                    df,
                    PSAR_STEP,
                    PSAR_MAX
                )

                if psar is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                last_close = float(
                    df["close"].iloc[-1]
                )

                if bullish:

                    signal = "BUY"

                else:

                    signal = "SELL"

                print(
                    f"[PSAR] {symbol} | "
                    f"Close={last_close} | "
                    f"PSAR={psar} | "
                    f"Signal={signal}"
                )

                # =====================================
                # IBA JEDNA POZÍCIA
                # =====================================

                positions = await get_positions(
                    connection,
                    symbol
                )

                if positions:

                    print(
                        f"[INFO] Pozícia už existuje: "
                        f"{len(positions)}"
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =====================================
                # ROVNAKÝ SIGNÁL – NEOTVÁRAŤ ZNOVA
                # =====================================

                if signal == last_signal:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =====================================
                # SYMBOL PARAMETRE
                # =====================================

                spec = await get_symbol_info(
                    connection,
                    symbol
                )

                if not spec:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                point = float(
                    spec.get(
                        "point",
                        0.01
                    )
                )

                digits = int(
                    spec.get(
                        "digits",
                        2
                    )
                )

                min_volume = spec.get(
                    "minVolume",
                    "neznáme"
                )

                max_volume = spec.get(
                    "maxVolume",
                    "neznáme"
                )

                volume_step = spec.get(
                    "volumeStep",
                    "neznáme"
                )

                min_stop = spec.get(
                    "minStopDistance",
                    "neznáme"
                )

                # =====================================
                # AKTUÁLNA CENA
                # =====================================

                price = await (
                    connection.get_symbol_price(
                        symbol
                    )
                )

                if signal == "BUY":

                    entry = float(
                        price["ask"]
                    )

                else:

                    entry = float(
                        price["bid"]
                    )

                # =====================================
                # SL / TP
                # =====================================

                if signal == "BUY":

                    sl = round(
                        entry
                        - SL_POINTS * point,
                        digits
                    )

                    tp = round(
                        entry
                        + TP_POINTS * point,
                        digits
                    )

                else:

                    sl = round(
                        entry
                        + SL_POINTS * point,
                        digits
                    )

                    tp = round(
                        entry
                        - TP_POINTS * point,
                        digits
                    )

                print(
                    "\n================================"
                )

                print(
                    f"SIGNAL: {signal}"
                )

                print(
                    f"Symbol: {symbol}"
                )

                print(
                    f"Lot: {LOT_SIZE}"
                )

                print(
                    f"Entry: {entry}"
                )

                print(
                    f"SL: {sl}"
                )

                print(
                    f"TP: {tp}"
                )

                print(
                    f"Point: {point}"
                )

                print(
                    f"Digits: {digits}"
                )

                print(
                    f"Min lot: {min_volume}"
                )

                print(
                    f"Max lot: {max_volume}"
                )

                print(
                    f"Lot step: {volume_step}"
                )

                print(
                    f"Min stop: {min_stop}"
                )

                print(
                    "clientId: VYPNUTÝ"
                )

                print(
                    "================================\n"
                )

                # =====================================
                # BUY
                # =====================================

                if signal == "BUY":

                    try:

                        result = await (
                            connection
                            .create_market_buy_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                stop_loss=sl,
                                take_profit=tp,
                                options={
                                    "comment": COMMENT
                                }
                            )
                        )

                        print(
                            "BUY OPENED:",
                            result
                        )

                        telegram(
                            "🟢 BUY OTVORENÝ\n\n"
                            f"Symbol: {symbol}\n"
                            f"Lot: {LOT_SIZE}\n"
                            f"Cena: {entry}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}"
                        )

                        last_signal = signal

                    except Exception as error:

                        detailed_error = (
                            get_error_details(
                                api,
                                error
                            )
                        )

                        telegram(
                            "❌ BUY NEBOL OTVORENÝ\n\n"
                            f"Symbol: {symbol}\n"
                            f"Lot: {LOT_SIZE}\n"
                            f"Cena: {entry}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n\n"
                            f"CHYBA:\n{error}\n\n"
                            f"DETAIL:\n"
                            f"{detailed_error}\n\n"
                            f"BTCUSD PARAMETRE:\n"
                            f"Point: {point}\n"
                            f"Digits: {digits}\n"
                            f"Min lot: {min_volume}\n"
                            f"Max lot: {max_volume}\n"
                            f"Lot step: {volume_step}\n"
                            f"Min stop: {min_stop}"
                        )

                # =====================================
                # SELL
                # =====================================

                elif signal == "SELL":

                    try:

                        result = await (
                            connection
                            .create_market_sell_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                stop_loss=sl,
                                take_profit=tp,
                                options={
                                    "comment": COMMENT
                                }
                            )
                        )

                        print(
                            "SELL OPENED:",
                            result
                        )

                        telegram(
                            "🔴 SELL OTVORENÝ\n\n"
                            f"Symbol: {symbol}\n"
                            f"Lot: {LOT_SIZE}\n"
                            f"Cena: {entry}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}"
                        )

                        last_signal = signal

                    except Exception as error:

                        detailed_error = (
                            get_error_details(
                                api,
                                error
                            )
                        )

                        telegram(
                            "❌ SELL NEBOL OTVORENÝ\n\n"
                            f"Symbol: {symbol}\n"
                            f"Lot: {LOT_SIZE}\n"
                            f"Cena: {entry}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n\n"
                            f"CHYBA:\n{error}\n\n"
                            f"DETAIL:\n"
                            f"{detailed_error}\n\n"
                            f"BTCUSD PARAMETRE:\n"
                            f"Point: {point}\n"
                            f"Digits: {digits}\n"
                            f"Min lot: {min_volume}\n"
                            f"Max lot: {max_volume}\n"
                            f"Lot step: {volume_step}\n"
                            f"Min stop: {min_stop}"
                        )

                await asyncio.sleep(
                    LOOP_SECONDS
                )

            except Exception:

                error_text = traceback.format_exc()

                print(
                    "⚠️ RIObot chyba v cykle:"
                )

                print(error_text)

                telegram(
                    "⚠️ RIObot chyba v cykle:\n\n"
                    f"{error_text}"
                )

                await asyncio.sleep(
                    LOOP_SECONDS
                )

    except Exception:

        error_text = traceback.format_exc()

        print(
            "❌ RIObot kritická chyba:"
        )

        print(error_text)

        telegram(
            "❌ RIObot kritická chyba:\n\n"
            f"{error_text}"
        )

    finally:

        try:

            if connection:
                await connection.close()

        except Exception:
            pass


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    flask_thread = Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    asyncio.run(main())
