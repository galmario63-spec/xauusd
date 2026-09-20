import os
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests


# =========================================================
# RAILWAY WEB SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Riobot PSAR M1 Active"


def run_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_server, daemon=True)
    t.start()


# =========================================================
# ENVIRONMENT
# =========================================================

TELEGRAM_TOKEN = os.getenv("T_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("T_CHAT")

METAAPI_TOKEN = os.getenv("M_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("M_ACC")


# =========================================================
# BOT SETTINGS
# =========================================================

SYMBOL_REQUEST = "BTCUSD"

TIMEFRAME = "1m"

# Tvoje Parabolic SAR nastavenie
PSAR_STEP = 0.80
PSAR_MAXIMUM = 0.40

# Centový účet
LOT_SIZE = 0.30

# TP / SL
TP_POINTS = 600.0
SL_POINTS = 1500.0

# BE
BE_TRIGGER = 250.0
BE_LOCK = 100.0

# Identifikácia robota
MAGIC = 26092026
COMMENT = "Riobot PSAR M1"


# =========================================================
# STATE
# =========================================================

last_processed_candle = None
startup_message_sent = False


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_TOKEN}/sendMessage"
        )

        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=5
        )

    except Exception as e:
        print(f"Telegram error: {e}")


# =========================================================
# PARABOLIC SAR
# =========================================================

def calculate_psar(candles, step, maximum):

    """
    Výpočet Parabolic SAR.

    direction:
        True  = BUY / bodky pod cenou
        False = SELL / bodky nad cenou
    """

    if len(candles) < 3:
        return [], []

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    sar = [None] * len(candles)
    direction = [None] * len(candles)

    # Prvý smer
    uptrend = closes[1] >= closes[0]

    if uptrend:
        sar[0] = lows[0]
        extreme_point = highs[0]
    else:
        sar[0] = highs[0]
        extreme_point = lows[0]

    acceleration = step

    direction[0] = uptrend

    for i in range(1, len(candles)):

        previous_sar = sar[i - 1]

        current_sar = (
            previous_sar
            + acceleration
            * (extreme_point - previous_sar)
        )

        # =====================================================
        # BUY TREND
        # =====================================================

        if uptrend:

            if i >= 2:
                current_sar = min(
                    current_sar,
                    lows[i - 1],
                    lows[i - 2]
                )
            else:
                current_sar = min(
                    current_sar,
                    lows[i - 1]
                )

            # PSAR sa otočil NAD cenu -> SELL
            if lows[i] < current_sar:

                uptrend = False

                current_sar = extreme_point
                extreme_point = lows[i]

                acceleration = step

            else:

                if highs[i] > extreme_point:

                    extreme_point = highs[i]

                    acceleration = min(
                        maximum,
                        acceleration + step
                    )

        # =====================================================
        # SELL TREND
        # =====================================================

        else:

            if i >= 2:
                current_sar = max(
                    current_sar,
                    highs[i - 1],
                    highs[i - 2]
                )
            else:
                current_sar = max(
                    current_sar,
                    highs[i - 1]
                )

            # PSAR sa otočil POD cenu -> BUY
            if highs[i] > current_sar:

                uptrend = True

                current_sar = extreme_point
                extreme_point = highs[i]

                acceleration = step

            else:

                if lows[i] < extreme_point:

                    extreme_point = lows[i]

                    acceleration = min(
                        maximum,
                        acceleration + step
                    )

        sar[i] = current_sar
        direction[i] = uptrend

    return sar, direction


# =========================================================
# FIND BTC SYMBOL
# =========================================================

async def find_symbol(connection):

    try:

        spec = await connection.get_symbol_specification(SYMBOL_REQUEST)
        if spec and spec.get("symbol"):
            return spec.get("symbol")

    except Exception as e:

        print(f"Symbol search error: {e}")

    return SYMBOL_REQUEST


# =========================================================
# BOT POSITION
# =========================================================

def is_bot_position(position):

    try:

        if int(position.get("magic", 0)) == MAGIC:
            return True

    except Exception:
        pass

    if position.get("comment") == COMMENT:
        return True

    return False


# =========================================================
# MAIN
# =========================================================

async def main():

    global last_processed_candle
    global startup_message_sent

    if not METAAPI_TOKEN:
        print("❌ Chýba M_TOKEN")
        return

    if not METAAPI_ACCOUNT_ID:
        print("❌ Chýba M_ACC")
        return

    api = MetaApi(METAAPI_TOKEN)

    while True:

        try:

            print("🔌 Pripájam MetaApi...")

            account = (
                await api.metatrader_account_api
                .get_account(METAAPI_ACCOUNT_ID)
            )

            # -------------------------------------------------
            # DEPLOY
            # -------------------------------------------------

            if account.state != "DEPLOYED":

                print("🚀 Deploy účtu...")

                await account.deploy()

            await account.wait_connected()

            connection = account.get_rpc_connection()

            await connection.connect()

            await connection.wait_synchronized()

            # -------------------------------------------------
            # SYMBOL
            # -------------------------------------------------

            symbol = await find_symbol(connection)

            print(f"₿ Obchodovaný symbol: {symbol}")

            # -------------------------------------------------
            # SYMBOL SPECIFICATION
            # -------------------------------------------------

            specification = await connection.get_symbol_specification(symbol)

            if not specification:

                print(
                    "⚠️ Nepodarilo sa načítať "
                    "specifikáciu symbolu."
                )

                await asyncio.sleep(10)
                continue

            point = float(
                specification.get("point", 0.01)
            )

            digits = int(
                specification.get("digits", 2)
            )

            min_volume = float(
                specification.get("minVolume", 0.01)
            )

            volume_step = float(
                specification.get("volumeStep", 0.01)
            )

            print(
                f"⚙️ {symbol} | "
                f"point={point} | "
                f"digits={digits} | "
                f"minLot={min_volume} | "
                f"step={volume_step}"
            )

            # -------------------------------------------------
            # TELEGRAM START
            # -------------------------------------------------

            if not startup_message_sent:

                send_telegram(
                    "🚀 RIObot PSAR M1 SPUSTENÝ\n\n"
                    f"Symbol: {symbol}\n"
                    f"Lot: {LOT_SIZE}\n"
                    f"PSAR Step: {PSAR_STEP}\n"
                    f"PSAR Maximum: {PSAR_MAXIMUM}\n"
                    f"TP: {TP_POINTS} points\n"
                    f"SL: {SL_POINTS} points\n"
                    f"BE trigger: +{BE_TRIGGER}\n"
                    f"BE lock: +{BE_LOCK}"
                )

                startup_message_sent = True

            # =================================================
            # MAIN LOOP
            # =================================================

            while True:

                try:

                    # -------------------------------------------------
                    # AKTUÁLNA CENA
                    # -------------------------------------------------

                    price = (
                        await connection
                        .get_symbol_price(symbol)
                    )

                    bid = float(price["bid"])
                    ask = float(price["ask"])

                    # -------------------------------------------------
                    # M1 CANDLES
                    # -------------------------------------------------

                    candles = (
                        await connection
                        .get_historical_candles(
                            symbol,
                            TIMEFRAME,
                            None,
                            150
                        )
                    )

                    if not candles or len(candles) < 10:

                        print(
                            "⏳ Čakám na M1 dáta..."
                        )

                        await asyncio.sleep(3)
                        continue

                    # -------------------------------------------------
                    # ZORADENIE OD NAJSTARŠEJ
                    # -------------------------------------------------

                    candles = sorted(
                        candles,
                        key=lambda x: x["time"]
                    )

                    # Posledná sviečka je aktuálne otvorená.
                    # PSAR vyhodnocujeme iba na uzavretých.
                    closed = candles[:-1]

                    if len(closed) < 5:

                        await asyncio.sleep(2)
                        continue

                    # -------------------------------------------------
                    # PSAR
                    # -------------------------------------------------

                    sar_values, directions = (
                        calculate_psar(
                            closed,
                            PSAR_STEP,
                            PSAR_MAXIMUM
                        )
                    )

                    if not sar_values:

                        await asyncio.sleep(2)
                        continue

                    # Posledná uzavretá sviečka
                    current_index = -1
                    previous_index = -2

                    current_sar = float(
                        sar_values[current_index]
                    )

                    previous_sar = float(
                        sar_values[previous_index]
                    )

                    current_direction = (
                        directions[current_index]
                    )

                    previous_direction = (
                        directions[previous_index]
                    )

                    candle_time = closed[
                        current_index
                    ]["time"]

                    # -------------------------------------------------
                    # VÝPIS
                    # -------------------------------------------------

                    print(
                        f"📊 M1 | "
                        f"SAR={current_sar:.{digits}f} | "
                        f"Smer="
                        f"{'BUY 🟢' if current_direction else 'SELL 🔴'}"
                    )

                    # =================================================
                    # POZÍCIE
                    # =================================================

                    positions = (
                        await connection.get_positions()
                    )

                    bot_positions = [
                        p for p in positions
                        if p.get("symbol") == symbol
                        and is_bot_position(p)
                    ]

                    # =================================================
                    # BE
                    # =================================================

                    for pos in bot_positions:

                        open_price = float(
                            pos["openPrice"]
                        )

                        current_sl = float(
                            pos.get("stopLoss") or 0
                        )

                        current_tp = pos.get(
                            "takeProfit"
                        )

                        # ---------------------------------------------
                        # BUY BE
                        # ---------------------------------------------

                        if (
                            pos["type"]
                            == "POSITION_TYPE_BUY"
                        ):

                            profit_points = (
                                bid - open_price
                            ) / point

                            if (
                                profit_points
                                >= BE_TRIGGER
                            ):

                                target_sl = (
                                    open_price
                                    + BE_LOCK * point
                                )

                                target_sl = round(
                                    target_sl,
                                    digits
                                )

                                if (
                                    current_sl == 0
                                    or current_sl < target_sl
                                ):

                                    try:

                                        await connection.modify_position(
                                            positionId=pos["id"],
                                            stop_loss=target_sl,
                                            take_profit=current_tp
                                        )

                                        print(
                                            f"🔒 BUY BE -> "
                                            f"{target_sl}"
                                        )

                                        send_telegram(
                                            f"🔒 BE BUY\n"
                                            f"{symbol}\n"
                                            f"SL: {target_sl}"
                                        )

                                    except Exception as e:

                                        print(
                                            f"❌ BUY BE error: {e}"
                                        )

                        # ---------------------------------------------
                        # SELL BE
                        # ---------------------------------------------

                        elif (
                            pos["type"]
                            == "POSITION_TYPE_SELL"
                        ):

                            profit_points = (
                                open_price - ask
                            ) / point

                            if (
                                profit_points
                                >= BE_TRIGGER
                            ):

                                target_sl = (
                                    open_price
                                    - BE_LOCK * point
                                )

                                target_sl = round(
                                    target_sl,
                                    digits
                                )

                                if (
                                    current_sl == 0
                                    or current_sl > target_sl
                                ):

                                    try:

                                        await connection.modify_position(
                                            positionId=pos["id"],
                                            stop_loss=target_sl,
                                            take_profit=current_tp
                                        )

                                        print(
                                            f"🔒 SELL BE -> "
                                            f"{target_sl}"
                                        )

                                        send_telegram(
                                            f"🔒 BE SELL\n"
                                            f"{symbol}\n"
                                            f"SL: {target_sl}"
                                        )

                                    except Exception as e:

                                        print(
                                            f"❌ SELL BE error: {e}"
                                        )

                    # =================================================
                    # NOVÁ UZAVRETÁ M1 SVIEČKA
                    # =================================================

                    if candle_time != last_processed_candle:

                        last_processed_candle = candle_time

                        # ---------------------------------------------
                        # PSAR OTOČENIE
                        # ---------------------------------------------

                        buy_signal = (
                            previous_direction is False
                            and current_direction is True
                        )

                        sell_signal = (
                            previous_direction is True
                            and current_direction is False
                        )

                        # =================================================
                        # BUY
                        # =================================================

                        if buy_signal:

                            print(
                                "🟢 PSAR OTOČENIE -> BUY"
                            )

                            send_telegram(
                                f"🟢 PSAR M1 BUY\n"
                                f"{symbol}\n"
                                f"SAR: {current_sar}"
                            )

                            # -----------------------------------------
                            # ZAVRIE SELL
                            # -----------------------------------------

                            for pos in bot_positions:

                                if (
                                    pos["type"]
                                    == "POSITION_TYPE_SELL"
                                ):

                                    try:

                                        await connection.close_position(
                                            positionId=pos["id"]
                                        )

                                        print(
                                            "🔄 SELL zatvorený"
                                        )

                                    except Exception as e:

                                        print(
                                            f"❌ Close SELL: {e}"
                                        )

                            # -----------------------------------------
                            # NAČÍTAME POZÍCIE ZNOVA
                            # -----------------------------------------

                            positions = (
                                await connection.get_positions()
                            )

                            bot_positions = [
                                p for p in positions
                                if p.get("symbol") == symbol
                                and is_bot_position(p)
                            ]

                            # -----------------------------------------
                            # OTVOR BUY
                            # -----------------------------------------

                            if not bot_positions:

                                sl = (
                                    ask
                                    - SL_POINTS * point
                                )

                                tp = (
                                    ask
                                    + TP_POINTS * point
                                )

                                sl = round(
                                    sl,
                                    digits
                                )

                                tp = round(
                                    tp,
                                    digits
                                )

                                try:

                                    result = (
                                        await connection
                                        .create_market_buy_order(
                                            symbol,
                                            LOT_SIZE,
                                            stop_loss=sl,
                                            take_profit=tp,
                                            options={
                                                "comment": COMMENT,
                                                "magic": MAGIC
                                            }
                                        )
                                    )

                                    print(
                                        f"🟢 BUY otvorený | "
                                        f"SL={sl} | "
                                        f"TP={tp}"
                                    )

                                    send_telegram(
                                        f"🟢 BUY OTVORENÝ\n\n"
                                        f"{symbol}\n"
                                        f"Lot: {LOT_SIZE}\n"
                                        f"SL: {sl}\n"
                                        f"TP: {tp}\n"
                                        f"PSAR: {current_sar}"
                                    )

                                except Exception as e:

                                    print(
                                        f"❌ BUY ERROR: {e}"
                                    )

                                    send_telegram(
                                        f"❌ BUY sa nepodarilo "
                                        f"otvoriť:\n{e}"
                                    )

                        # =================================================
                        # SELL
                        # =================================================

                        elif sell_signal:

                            print(
                                "🔴 PSAR OTOČENIE -> SELL"
                            )

                            send_telegram(
                                f"🔴 PSAR M1 SELL\n"
                                f"{symbol}\n"
                                f"SAR: {current_sar}"
                            )

                            # -----------------------------------------
                            # ZAVRIE BUY
                            # -----------------------------------------

                            for pos in bot_positions:

                                if (
                                    pos["type"]
                                    == "POSITION_TYPE_BUY"
                                ):

                                    try:

                                        await connection.close_position(
                                            positionId=pos["id"]
                                        )

                                        print(
                                            "🔄 BUY zatvorený"
                                        )

                                    except Exception as e:

                                        print(
                                            f"❌ Close BUY: {e}"
                                        )

                            # -----------------------------------------
                            # NAČÍTAME POZÍCIE ZNOVA
                            # -----------------------------------------

                            positions = (
                                await connection.get_positions()
                            )

                            bot_positions = [
                                p for p in positions
                                if p.get("symbol") == symbol
                                and is_bot_position(p)
                            ]

                            # -----------------------------------------
                            # OTVOR SELL
                            # -----------------------------------------

                            if not bot_positions:

                                sl = (
                                    bid
                                    + SL_POINTS * point
                                )

                                tp = (
                                    bid
                                    - TP_POINTS * point
                                )

                                sl = round(
                                    sl,
                                    digits
                                )

                                tp = round(
                                    tp,
                                    digits
                                )

                                try:

                                    result = (
                                        await connection
                                        .create_market_sell_order(
                                            symbol,
                                            LOT_SIZE,
                                            stop_loss=sl,
                                            take_profit=tp,
                                            options={
                                                "comment": COMMENT,
                                                "magic": MAGIC
                                            }
                                        )
                                    )

                                    print(
                                        f"🔴 SELL otvorený | "
                                        f"SL={sl} | "
                                        f"TP={tp}"
                                    )

                                    send_telegram(
                                        f"🔴 SELL OTVORENÝ\n\n"
                                        f"{symbol}\n"
                                        f"Lot: {LOT_SIZE}\n"
                                        f"SL: {sl}\n"
                                        f"TP: {tp}\n"
                                        f"PSAR: {current_sar}"
                                    )

                                except Exception as e:

                                    print(
                                        f"❌ SELL ERROR: {e}"
                                    )

                                    send_telegram(
                                        f"❌ SELL sa nepodarilo "
                                        f"otvoriť:\n{e}"
                                    )

                    # -------------------------------------------------
                    # KONTROLA KAŽDÉ 2 SEKUNDY
                    # -------------------------------------------------

                    await asyncio.sleep(2)

                except Exception as inner_error:

                    print(
                        f"⚠️ Chyba slučky: "
                        f"{inner_error}"
                    )

                    await asyncio.sleep(5)

                    break

        except Exception as outer_error:

            print(
                f"❌ Chyba MetaApi: "
                f"{outer_error}"
            )

            send_telegram(
                f"⚠️ Riobot chyba:\n"
                f"{outer_error}"
            )

            await asyncio.sleep(10)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    keep_alive()

    asyncio.run(main())
