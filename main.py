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
# METAAPI / TELEGRAM
# =========================================================

TELEGRAM_TOKEN = os.getenv("T_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("T_CHAT")

METAAPI_TOKEN = os.getenv("M_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("M_ACC")


# =========================================================
# NASTAVENIA ROBOTA
# =========================================================

SYMBOL_REQUEST = "BTCUSD"

# CENTOVÝ ÚČET
LOT_SIZE = 0.30

# TAKE PROFIT / STOP LOSS
TP_POINTS = 600.0
SL_POINTS = 1500.0

# MAGIC / COMMENT
MAGIC = 26092026
COMMENT = "Riobot PSAR"


# =========================================================
# BREAK EVEN
# =========================================================

# BE sa aktivuje pri +250 bodoch
BE_TRIGGER = 250.0

# Po aktivácii BE nechá +100 bodov
BE_LOCK = 100.0


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
# PARABOLIC SAR
# =========================================================

def calculate_psar(candles):

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    if len(highs) < 3:
        return None, None

    psar = lows[0]

    af = 0.02
    max_af = 0.20

    ep = highs[0]

    trend = 1

    for i in range(1, len(highs)):

        prev_psar = psar

        # -------------------------------------------------
        # UPTREND
        # -------------------------------------------------

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

        # -------------------------------------------------
        # DOWNTREND
        # -------------------------------------------------

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
# BREAK EVEN
# =========================================================

async def manage_break_even(
    connection,
    positions,
    symbol,
    point,
    digits
):

    for position in positions:

        try:

            if position.get("symbol") != symbol:
                continue

            if not is_bot_position(position):
                continue

            position_id = position.get("id")

            if not position_id:
                continue

            open_price = float(
                position.get("openPrice", 0)
            )

            current_price = float(
                position.get("currentPrice", 0)
            )

            current_sl = float(
                position.get("stopLoss", 0) or 0
            )

            position_type = str(
                position.get("type", "")
            ).lower()


            # =================================================
            # BUY
            # =================================================

            if "buy" in position_type:

                profit_points = (
                    current_price - open_price
                ) / point

                if profit_points >= BE_TRIGGER:

                    new_sl = round(
                        open_price + BE_LOCK * point,
                        digits
                    )

                    if current_sl == 0 or new_sl > current_sl:

                        try:

                            result = await connection.modify_position(
                                position_id=position_id,
                                stop_loss=new_sl,
                                take_profit=position.get("takeProfit")
                            )

                            print(
                                f"🟢 BE BUY AKTIVOVANÝ | "
                                f"Profit: {profit_points:.1f} | "
                                f"Nový SL: {new_sl}"
                            )

                            send_telegram(
                                f"🛡️ BE BUY AKTIVOVANÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Profit: {profit_points:.1f} bodov\n"
                                f"Nový SL: {new_sl}\n"
                                f"BE lock: +{BE_LOCK} bodov"
                            )

                        except Exception as be_error:

                            print(
                                f"❌ BE BUY ERROR: {be_error}"
                            )

                            send_telegram(
                                f"❌ BE BUY ERROR\n{be_error}"
                            )


            # =================================================
            # SELL
            # =================================================

            elif "sell" in position_type:

                profit_points = (
                    open_price - current_price
                ) / point

                if profit_points >= BE_TRIGGER:

                    new_sl = round(
                        open_price - BE_LOCK * point,
                        digits
                    )

                    if current_sl == 0 or new_sl < current_sl:

                        try:

                            result = await connection.modify_position(
                                position_id=position_id,
                                stop_loss=new_sl,
                                take_profit=position.get("takeProfit")
                            )

                            print(
                                f"🔴 BE SELL AKTIVOVANÝ | "
                                f"Profit: {profit_points:.1f} | "
                                f"Nový SL: {new_sl}"
                            )

                            send_telegram(
                                f"🛡️ BE SELL AKTIVOVANÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Profit: {profit_points:.1f} bodov\n"
                                f"Nový SL: {new_sl}\n"
                                f"BE lock: +{BE_LOCK} bodov"
                            )

                        except Exception as be_error:

                            print(
                                f"❌ BE SELL ERROR: {be_error}"
                            )

                            send_telegram(
                                f"❌ BE SELL ERROR\n{be_error}"
                            )

        except Exception as error:

            print(f"❌ Chyba BE: {error}")


# =========================================================
# HLAVNÝ BOT
# =========================================================

async def main():

    global startup_message_sent

    if not METAAPI_TOKEN:

        print("❌ Chýba M_TOKEN")

        send_telegram(
            "❌ RIObot ERROR\nChýba M_TOKEN"
        )

        return


    if not METAAPI_ACCOUNT_ID:

        print("❌ Chýba M_ACC")

        send_telegram(
            "❌ RIObot ERROR\nChýba M_ACC"
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

                print("🚀 Deployujem účet...")

                await account.deploy()


            # -------------------------------------------------
            # CONNECTION
            # -------------------------------------------------

            print("⏳ Čakám na MT5...")

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
                symbol=symbol
            )


            if not specification:

                send_telegram(
                    f"❌ Symbol {symbol} nebol nájdený."
                )

                await asyncio.sleep(30)

                continue


            point = float(
                specification.get("point", 0.01)
            )

            digits = int(
                specification.get("digits", 2)
            )


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
                    f"TP: {TP_POINTS} bodov\n"
                    f"SL: {SL_POINTS} bodov\n"
                    f"BE trigger: {BE_TRIGGER} bodov\n"
                    f"BE lock: +{BE_LOCK} bodov\n"
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
                        symbol=symbol
                    )

                    bid = float(price["bid"])
                    ask = float(price["ask"])


                    # -----------------------------------------
                    # POZÍCIE
                    # -----------------------------------------

                    positions = await connection.get_positions()


                    bot_positions = [
                        p
                        for p in positions
                        if p.get("symbol") == symbol
                        and is_bot_position(p)
                    ]


                    # -----------------------------------------
                    # BREAK EVEN
                    # -----------------------------------------

                    if bot_positions:

                        await manage_break_even(
                            connection,
                            bot_positions,
                            symbol,
                            point,
                            digits
                        )


                    # =================================================
                    # HISTORICKÉ SVIEČKY
                    # =================================================

                    candles = await account.get_historical_candles(
                        symbol=symbol,
                        timeframe="1m",
                        start_time=None,
                        limit=50
                    )


                    if not candles or len(candles) < 10:

                        print("⚠️ Nedostatok sviečok")

                        await asyncio.sleep(5)

                        continue


                    # -----------------------------------------
                    # PSAR
                    # -----------------------------------------

                    trend, psar = calculate_psar(candles)


                    if trend is None:

                        await asyncio.sleep(5)

                        continue


                    signal = (
                        "BUY"
                        if trend == 1
                        else "SELL"
                    )


                    print(
                        f"📈 {symbol} | "
                        f"Bid: {bid} | "
                        f"Ask: {ask} | "
                        f"PSAR: {psar} | "
                        f"Signal: {signal}"
                    )


                    # =================================================
                    # AK UŽ MÁME POZÍCIU
                    # =================================================

                    if bot_positions:

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
                            f"🟢 BUY SIGNÁL\n"
                            f"Cena: {ask}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n"
                            f"Lot: {LOT_SIZE}"
                        )


                        try:

                            result = await connection.create_market_buy_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                stop_loss=sl,
                                take_profit=tp,
                                options={
                                    "comment": COMMENT,
                                    "clientId": str(MAGIC)
                                }
                            )


                            print(
                                f"✅ BUY RESULT: {result}"
                            )


                            send_telegram(
                                f"🟢 PSAR BUY OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {ask}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n"
                                f"BE: +{BE_TRIGGER} → +{BE_LOCK}\n\n"
                                f"Result: {result}"
                            )


                        except Exception as order_error:

                            print(
                                f"❌ BUY ERROR: {order_error}"
                            )

                            send_telegram(
                                f"❌ BUY NEBOL OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {ask}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n\n"
                                f"CHYBA:\n{order_error}"
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
                            f"🔴 SELL SIGNÁL\n"
                            f"Cena: {bid}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n"
                            f"Lot: {LOT_SIZE}"
                        )


                        try:

                            result = await connection.create_market_sell_order(
                                symbol=symbol,
                                volume=LOT_SIZE,
                                stop_loss=sl,
                                take_profit=tp,
                                options={
                                    "comment": COMMENT,
                                    "clientId": str(MAGIC)
                                }
                            )


                            print(
                                f"✅ SELL RESULT: {result}"
                            )


                            send_telegram(
                                f"🔴 PSAR SELL OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {bid}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n"
                                f"BE: +{BE_TRIGGER} → +{BE_LOCK}\n\n"
                                f"Result: {result}"
                            )


                        except Exception as order_error:

                            print(
                                f"❌ SELL ERROR: {order_error}"
                            )

                            send_telegram(
                                f"❌ SELL NEBOL OTVORENÝ\n\n"
                                f"Symbol: {symbol}\n"
                                f"Lot: {LOT_SIZE}\n"
                                f"Cena: {bid}\n"
                                f"SL: {sl}\n"
                                f"TP: {tp}\n\n"
                                f"CHYBA:\n{order_error}"
                            )


                    # -----------------------------------------
                    # ČAKANIE
                    # -----------------------------------------

                    await asyncio.sleep(15)


                except Exception as inner_error:

                    print(
                        f"⚠️ RIObot chyba v cykle:\n"
                        f"{inner_error}"
                    )

                    send_telegram(
                        f"⚠️ RIObot chyba v cykle:\n"
                        f"{inner_error}"
                    )

                    await asyncio.sleep(5)


        except Exception as outer_error:

            print(
                f"🔴 CHYBA PRIPOJENIA:\n"
                f"{outer_error}"
            )

            send_telegram(
                f"🔴 RIObot MetaApi problém:\n"
                f"{outer_error}"
            )

            await asyncio.sleep(15)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    keep_alive()

    asyncio.run(main())
