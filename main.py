import os
import asyncio
from flask import Flask
from threading import Thread

requests_lib = __import__("requests")

app = Flask(__name__)


@app.route("/")
def home():
    return "Riobot PSAR Active"


def run_server():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_server, daemon=True)
    t.start()


# =========================================================
# NASTAVENIA
# =========================================================

TELEGRAM_TOKEN = os.getenv("T_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("T_CHAT")

METAAPI_TOKEN = os.getenv("M_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("M_ACC")

SYMBOL_REQUEST = "BTCUSD"

# CENTOVÝ ÚČET
LOT_SIZE = 0.30

TP_POINTS = 600.0
SL_POINTS = 1500.0

MAGIC = 26092026
COMMENT = "Riobot PSAR"

startup_message_sent = False


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        requests_lib.post(
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
# KONTROLA POZÍCIE ROBOTA
# =========================================================

def is_bot_position(position):

    try:
        if int(position.get("magic", 0)) == MAGIC:
            return True
    except Exception:
        pass

    return position.get("comment") == COMMENT


# =========================================================
# POMOCNÉ FUNKCIE
# =========================================================

def get_value(data, key, default=None):

    try:
        value = data.get(key)

        if value is None:
            return default

        return value

    except Exception:
        return default


def calculate_psar(candles):

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    if len(highs) < 3:
        return None, None

    # -----------------------------------------------------
    # Parabolic SAR
    # -----------------------------------------------------

    psar = lows[0]

    af = 0.02
    max_af = 0.20

    ep = highs[0]

    trend = 1

    for i in range(1, len(highs)):

        prev_psar = psar

        if trend == 1:

            psar = prev_psar + af * (ep - prev_psar)

            psar = min(
                psar,
                lows[i - 1],
                lows[max(0, i - 2)]
            )

            if lows[i] < psar:

                trend = -1

                psar = ep
                ep = lows[i]

                af = 0.02

            else:

                if highs[i] > ep:

                    ep = highs[i]

                    af = min(
                        af + 0.02,
                        max_af
                    )

        else:

            psar = prev_psar - af * (prev_psar - ep)

            psar = max(
                psar,
                highs[i - 1],
                highs[max(0, i - 2)]
            )

            if highs[i] > psar:

                trend = 1

                psar = ep
                ep = highs[i]

                af = 0.02

            else:

                if lows[i] < ep:

                    ep = lows[i]

                    af = min(
                        af + 0.02,
                        max_af
                    )

    return trend, psar


# =========================================================
# HLAVNÝ BOT
# =========================================================

async def main():

    global startup_message_sent

    if not METAAPI_TOKEN:

        print("❌ Chýba M_TOKEN")

        send_telegram(
            "❌ RIObot ERROR\n"
            "Chýba M_TOKEN"
        )

        return

    if not METAAPI_ACCOUNT_ID:

        print("❌ Chýba M_ACC")

        send_telegram(
            "❌ RIObot ERROR\n"
            "Chýba M_ACC"
        )

        return


    from metaapi_cloud_sdk import MetaApi

    api = MetaApi(METAAPI_TOKEN)


    while True:

        try:

            print("🔄 Pripájam MetaApi...")

            account = await api.metatrader_account_api.get_account(
                METAAPI_ACCOUNT_ID
            )


            # -------------------------------------------------
            # DEPLOY
            # -------------------------------------------------

            if account.state != "DEPLOYED":

                print("🚀 Účet nie je DEPLOYED - spúšťam...")

                await account.deploy()


            # -------------------------------------------------
            # CONNECTION
            # -------------------------------------------------

            print("⏳ Čakám na MT5 pripojenie...")

            await account.wait_connected()

            connection = account.get_rpc_connection()

            await connection.connect()

            await connection.wait_synchronized()


            print("✅ MT5 pripojené")


            # -------------------------------------------------
            # SYMBOL
            # -------------------------------------------------

            symbol = SYMBOL_REQUEST

            specification = await connection.get_symbol_specification(
                symbol
            )

            if not specification:

                error = (
                    f"❌ SYMBOL {symbol} NEBOL NÁJDENÝ!"
                )

                print(error)

                send_telegram(error)

                await asyncio.sleep(30)

                continue


            point = float(
                get_value(
                    specification,
                    "point",
                    0.01
                )
            )

            digits = int(
                get_value(
                    specification,
                    "digits",
                    2
                )
            )


            # -------------------------------------------------
            # INFO O SYMBOL
            # -------------------------------------------------

            print(
                f"📊 SYMBOL: {symbol}\n"
                f"Point: {point}\n"
                f"Digits: {digits}"
            )


            if not startup_message_sent:

                send_telegram(
                    f"🚀 RIObot PSAR ŠTART\n\n"
                    f"Symbol: {symbol}\n"
                    f"Lot: {LOT_SIZE}\n"
                    f"TP: {TP_POINTS} points\n"
                    f"SL: {SL_POINTS} points\n"
                    f"Point: {point}\n"
                    f"Digits: {digits}"
                )

                startup_message_sent = True


            # =================================================
            # HLAVNÝ CYKLUS
            # =================================================

            while True:

                try:

                    # -----------------------------------------
                    # CENA
                    # -----------------------------------------

                    price = await connection.get_symbol_price(
                        symbol
                    )

                    bid = float(price["bid"])
                    ask = float(price["ask"])


                    # -----------------------------------------
                    # SVIEČKY
                    # -----------------------------------------

                    candles = await connection.get_historical_candles(
                        symbol,
                        "1m",
                        50
                    )


                    if not candles or len(candles) < 10:

                        print("⚠️ Málo sviečok")

                        await asyncio.sleep(5)

                        continue


                    # -----------------------------------------
                    # PSAR
                    # -----------------------------------------

                    trend, psar = calculate_psar(candles)


                    if trend is None:

                        await asyncio.sleep(5)

                        continue


                    if trend == 1:

                        signal = "BUY"

                    else:

                        signal = "SELL"


                    print(
                        f"📈 {symbol} | "
                        f"Bid: {bid} | "
                        f"Ask: {ask} | "
                        f"PSAR: {psar} | "
                        f"Signal: {signal}"
                    )


                    # -----------------------------------------
                    # POZÍCIE ROBOTA
                    # -----------------------------------------

                    positions = await connection.get_positions()

                    bot_positions = [
                        p
                        for p in positions
                        if p.get("symbol") == symbol
                        and is_bot_position(p)
                    ]


                    # -----------------------------------------
                    # AK UŽ MÁME POZÍCIU
                    # -----------------------------------------

                    if bot_positions:

                        print(
                            f"ℹ️ Robot už má otvorenú pozíciu: "
                            f"{len(bot_positions)}"
                        )

                        await asyncio.sleep(15)

                        continue


                    # =================================================
                    # BUY
                    # =================================================

                    if trend == 1:

                        sl = round(
                            ask - SL_POINTS * point,
                            digits
                        )

                        tp = round(
                            ask + TP_POINTS * point,
                            digits
                        )


                        print(
                            "🟢 PSAR BUY SIGNÁL\n"
                            f"Cena: {ask}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n"
                            f"Lot: {LOT_SIZE}"
                        )


                        try:

                            result = await connection.create_market_buy_order(
                                symbol,
                                LOT_SIZE,
                                sl,
                                tp,
                                {
                                    "comment": COMMENT
                                }
                            )


                            print(
                                f"✅ BUY ODPOVEĎ:\n{result}"
                            )


                            send_telegram(
                                f"🟢 PSAR BUY OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {ask}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n\n"
                                f"Výsledok:\n{result}"
                            )


                        except Exception as order_error:

                            print(
                                f"❌ BUY ORDER ERROR:\n"
                                f"{order_error}"
                            )


                            send_telegram(
                                f"❌ BUY NEBOL OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {ask}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n\n"
                                f"CHYBA:\n"
                                f"{order_error}"
                            )


                    # =================================================
                    # SELL
                    # =================================================

                    elif trend == -1:

                        sl = round(
                            bid + SL_POINTS * point,
                            digits
                        )

                        tp = round(
                            bid - TP_POINTS * point,
                            digits
                        )


                        print(
                            "🔴 PSAR SELL SIGNÁL\n"
                            f"Cena: {bid}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n"
                            f"Lot: {LOT_SIZE}"
                        )


                        try:

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
                                f"✅ SELL ODPOVEĎ:\n{result}"
                            )


                            send_telegram(
                                f"🔴 PSAR SELL OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {bid}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n\n"
                                f"Výsledok:\n{result}"
                            )


                        except Exception as order_error:

                            print(
                                f"❌ SELL ORDER ERROR:\n"
                                f"{order_error}"
                            )


                            send_telegram(
                                f"❌ SELL NEBOL OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {bid}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n\n"
                                f"CHYBA:\n"
                                f"{order_error}"
                            )


                    # -----------------------------------------
                    # ČAKANIE
                    # -----------------------------------------

                    await asyncio.sleep(15)


                except Exception as inner_error:

                    print(
                        f"❌ CHYBA V CYKLE:\n"
                        f"{inner_error}"
                    )

                    send_telegram(
                        f"⚠️ RIObot chyba v cykle:\n"
                        f"{inner_error}"
                    )

                    await asyncio.sleep(5)


        except Exception as outer_error:

            print(
                f"❌ CHYBA PRIPOJENIA:\n"
                f"{outer_error}"
            )

            send_telegram(
                f"🔴 RIObot problém s MetaApi:\n"
                f"{outer_error}"
            )

            await asyncio.sleep(15)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    keep_alive()

    asyncio.run(main())
