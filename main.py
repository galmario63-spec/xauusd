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
LOT_SIZE = 0.30

PSAR_STEP = 0.02
PSAR_MAX = 0.20

EMA_PERIOD = 50

TP_POINTS = 1500.0
SL_POINTS = 1500.0

BE_TRIGGER = 500.0
BE_LOCK = 100.0

COMMENT = "Riobot 5M+1M BE+PSAR"
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
    return "RIObot is running"


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
            psar[i] = previous_psar + af * (ep - previous_psar)

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
                af = min(af + step, max_af)

        else:
            psar[i] = previous_psar + af * (ep - previous_psar)

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
                af = min(af + step, max_af)

    return psar[-1], bull


# =========================================================
# EMA
# =========================================================

def calculate_ema(df, period=50):
    return (
        df["close"]
        .ewm(span=period, adjust=False)
        .mean()
    )


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(connection, symbol):
    try:
        return await connection.get_symbol_specification(symbol)

    except Exception as e:
        print("Symbol specification chyba:", e)
        return None


# =========================================================
# POZÍCIE
# =========================================================

async def get_positions(connection, symbol):
    try:
        positions = await connection.get_positions()

        return [
            position
            for position in positions
            if position.get("symbol") == symbol
        ]

    except Exception as e:
        print("Chyba pri načítaní pozícií:", e)
        return []


# =========================================================
# UZAVRETÉ SVIEČKY
# =========================================================

async def get_closed_candles(
    account,
    symbol,
    timeframe,
    limit=100,
    minimum=10
):
    try:
        candles = await account.get_historical_candles(
            symbol=symbol,
            timeframe=timeframe,
            start_time=None,
            limit=limit
        )

        if not candles:
            return None

        df = pd.DataFrame(candles)

        if df.empty:
            return None

        if "time" in df.columns:
            df = df.sort_values("time")

        required = ["open", "high", "low", "close"]

        if not all(column in df.columns for column in required):
            return None

        for column in required:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df = df.dropna(subset=required)

        if len(df) < minimum + 1:
            return None

        # Posledná sviečka sa ešte tvorí.
        closed_df = df.iloc[:-1].copy()

        if len(closed_df) < minimum:
            return None

        return closed_df

    except Exception:
        print(
            f"Chyba dát {timeframe}:",
            traceback.format_exc()
        )
        return None


# =========================================================
# BREAK EVEN
# =========================================================

async def manage_break_even(connection, symbol):
    try:
        positions = await get_positions(connection, symbol)

        if not positions:
            return

        spec = await get_symbol_info(connection, symbol)

        if not spec:
            return

        point = float(spec.get("point", 0.01))
        digits = int(spec.get("digits", 2))

        for position in positions:
            try:
                position_id = position.get("id")
                position_type = position.get("type")

                open_price = float(position.get("openPrice"))
                current_price = float(position.get("currentPrice"))

                current_sl = position.get("stopLoss")
                take_profit = position.get("takeProfit")

                if position_type == "POSITION_TYPE_BUY":
                    profit_points = (
                        current_price - open_price
                    ) / point

                    if profit_points >= BE_TRIGGER:
                        new_sl = round(
                            open_price + BE_LOCK * point,
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
                                f"Trigger: +{BE_TRIGGER:.0f} bodov\n"
                                f"Zamknuté: +{BE_LOCK:.0f} bodov"
                            )

                elif position_type == "POSITION_TYPE_SELL":
                    profit_points = (
                        open_price - current_price
                    ) / point

                    if profit_points >= BE_TRIGGER:
                        new_sl = round(
                            open_price - BE_LOCK * point,
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
                                f"Trigger: +{BE_TRIGGER:.0f} bodov\n"
                                f"Zamknuté: +{BE_LOCK:.0f} bodov"
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
# PSAR TRAILING - AŽ PO +500 BODOCH
# =========================================================

async def manage_psar_stop(connection, account, symbol):
    try:
        positions = await get_positions(connection, symbol)

        if not positions:
            return

        spec = await get_symbol_info(connection, symbol)

        if not spec:
            return

        point = float(spec.get("point", 0.01))
        digits = int(spec.get("digits", 2))

        min_stop_raw = spec.get("minStopDistance")

        try:
            min_stop_points = float(min_stop_raw)
        except Exception:
            min_stop_points = 0.0

        min_distance = min_stop_points * point

        df_5m = await get_closed_candles(
            account,
            symbol,
            "5m",
            limit=100,
            minimum=10
        )

        if df_5m is None:
            return

        psar, _ = calculate_psar(
            df_5m,
            PSAR_STEP,
            PSAR_MAX
        )

        if psar is None:
            return

        psar = float(psar)

        price = await connection.get_symbol_price(symbol)

        bid = float(price["bid"])
        ask = float(price["ask"])

        for position in positions:
            try:
                position_id = position.get("id")
                position_type = position.get("type")

                open_price = float(position.get("openPrice"))

                current_sl = position.get("stopLoss")
                take_profit = position.get("takeProfit")

                # BUY
                if position_type == "POSITION_TYPE_BUY":
                    profit_points = (
                        bid - open_price
                    ) / point

                    # Trailing až od +500.
                    if profit_points < BE_TRIGGER:
                        continue

                    candidate_sl = round(psar, digits)

                    # PSAR musí byť pod cenou.
                    if candidate_sl >= bid:
                        continue

                    # PSAR nesmie zhoršiť BE +100.
                    be_floor = round(
                        open_price + BE_LOCK * point,
                        digits
                    )

                    candidate_sl = max(
                        candidate_sl,
                        be_floor
                    )

                    candidate_sl = round(
                        candidate_sl,
                        digits
                    )

                    if candidate_sl >= bid:
                        continue

                    if (
                        min_distance > 0
                        and bid - candidate_sl < min_distance
                    ):
                        continue

                    # SL iba smerom nahor.
                    if (
                        current_sl is not None
                        and candidate_sl <= float(current_sl)
                    ):
                        continue

                    await connection.modify_position(
                        position_id=position_id,
                        stop_loss=candidate_sl,
                        take_profit=take_profit
                    )

                    telegram(
                        "📈 PSAR TRAILING – BUY\n\n"
                        f"Symbol: {symbol}\n"
                        f"Profit: {profit_points:.0f} bodov\n"
                        f"5M PSAR: {psar:.2f}\n"
                        f"Nový SL: {candidate_sl}"
                    )

                # SELL
                elif position_type == "POSITION_TYPE_SELL":
                    profit_points = (
                        open_price - ask
                    ) / point

                    # Trailing až od +500.
                    if profit_points < BE_TRIGGER:
                        continue

                    candidate_sl = round(psar, digits)

                    # PSAR musí byť nad cenou.
                    if candidate_sl <= ask:
                        continue

                    # PSAR nesmie zhoršiť BE +100.
                    be_ceiling = round(
                        open_price - BE_LOCK * point,
                        digits
                    )

                    candidate_sl = min(
                        candidate_sl,
                        be_ceiling
                    )

                    candidate_sl = round(
                        candidate_sl,
                        digits
                    )

                    if candidate_sl <= ask:
                        continue

                    if (
                        min_distance > 0
                        and candidate_sl - ask < min_distance
                    ):
                        continue

                    # SL iba smerom nadol.
                    if (
                        current_sl is not None
                        and candidate_sl >= float(current_sl)
                    ):
                        continue

                    await connection.modify_position(
                        position_id=position_id,
                        stop_loss=candidate_sl,
                        take_profit=take_profit
                    )

                    telegram(
                        "📉 PSAR TRAILING – SELL\n\n"
                        f"Symbol: {symbol}\n"
                        f"Profit: {profit_points:.0f} bodov\n"
                        f"5M PSAR: {psar:.2f}\n"
                        f"Nový SL: {candidate_sl}"
                    )

            except Exception:
                print(
                    "PSAR trailing chyba:",
                    traceback.format_exc()
                )

    except Exception:
        print(
            "PSAR trailing systém chyba:",
            traceback.format_exc()
        )


# =========================================================
# HLAVNÝ BOT
# =========================================================

async def main():
    if not M_TOKEN:
        telegram("❌ Chýba M_TOKEN")
        return

    if not M_ACC:
        telegram("❌ Chýba M_ACC")
        return

    api = MetaApi(M_TOKEN)
    connection = None

    try:
        account = await (
            api.metatrader_account_api
            .get_account(M_ACC)
        )

        await account.wait_connected()

        connection = account.get_rpc_connection()

        await connection.connect()
        await connection.wait_synchronized()

        telegram(
            "🟢 RIObot spustený – TEST 1:1\n\n"
            f"Symbol: {SYMBOL_REQUEST}\n"
            f"Lot: {LOT_SIZE}\n\n"
            "Smer: 5M PSAR + EMA50\n"
            "Vstup: 1M PSAR FLIP\n\n"
            f"TP: {TP_POINTS} bodov\n"
            f"SL: {SL_POINTS} bodov\n"
            f"BE trigger: +{BE_TRIGGER} bodov\n"
            f"BE lock: +{BE_LOCK} bodov\n\n"
            "PSAR trailing: až po BE\n"
            "1 pozícia: ON\n"
            "clientId: VYPNUTÝ"
        )

        last_processed_m1 = None

        while True:
            try:
                symbol = SYMBOL_REQUEST

                # Najprv BE.
                await manage_break_even(
                    connection,
                    symbol
                )

                # Potom trailing.
                await manage_psar_stop(
                    connection,
                    account,
                    symbol
                )

                # 5M trend.
                df_5m = await get_closed_candles(
                    account,
                    symbol,
                    "5m",
                    limit=100,
                    minimum=55
                )

                # M1 vstup.
                df_1m = await get_closed_candles(
                    account,
                    symbol,
                    "1m",
                    limit=100,
                    minimum=20
                )

                if df_5m is None or df_1m is None:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                # 5M EMA50.
                ema_series = calculate_ema(
                    df_5m,
                    EMA_PERIOD
                )

                ema50_5m = float(
                    ema_series.iloc[-1]
                )

                close_5m = float(
                    df_5m["close"].iloc[-1]
                )

                # 5M PSAR.
                psar_5m, bull_5m = calculate_psar(
                    df_5m,
                    PSAR_STEP,
                    PSAR_MAX
                )

                if psar_5m is None:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                psar_5m = float(psar_5m)

                trend_buy = (
                    bull_5m is True
                    and close_5m > ema50_5m
                )

                trend_sell = (
                    bull_5m is False
                    and close_5m < ema50_5m
                )

                # M1 PSAR FLIP.
                previous_1m = df_1m.iloc[:-1].copy()
                current_1m = df_1m.copy()

                (
                    previous_psar_1m,
                    previous_bull_1m
                ) = calculate_psar(
                    previous_1m,
                    PSAR_STEP,
                    PSAR_MAX
                )

                (
                    current_psar_1m,
                    current_bull_1m
                ) = calculate_psar(
                    current_1m,
                    PSAR_STEP,
                    PSAR_MAX
                )

                if (
                    previous_psar_1m is None
                    or current_psar_1m is None
                ):
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                current_psar_1m = float(
                    current_psar_1m
                )

                psar_flip_buy_1m = (
                    previous_bull_1m is False
                    and current_bull_1m is True
                )

                psar_flip_sell_1m = (
                    previous_bull_1m is True
                    and current_bull_1m is False
                )

                # Finálny signál.
                if trend_buy and psar_flip_buy_1m:
                    signal = "BUY"

                elif trend_sell and psar_flip_sell_1m:
                    signal = "SELL"

                else:
                    signal = None

                if "time" in df_1m.columns:
                    m1_candle_id = str(
                        df_1m["time"].iloc[-1]
                    )
                else:
                    m1_candle_id = str(
                        df_1m.index[-1]
                    )

                print("\n==============================")
                print(f"[5M TREND] {symbol}")
                print(f"Close: {close_5m}")
                print(f"EMA50: {ema50_5m}")
                print(f"PSAR: {psar_5m}")
                print(f"Bullish: {bull_5m}")
                print(f"BUY trend: {trend_buy}")
                print(f"SELL trend: {trend_sell}")
                print("------------------------------")
                print("[M1 ENTRY]")
                print(f"PSAR: {current_psar_1m}")
                print(f"Bullish: {current_bull_1m}")
                print(f"BUY flip: {psar_flip_buy_1m}")
                print(f"SELL flip: {psar_flip_sell_1m}")
                print(f"FINAL SIGNAL: {signal}")
                print("==============================")

                # Iba jedna pozícia.
                positions = await get_positions(
                    connection,
                    symbol
                )

                if positions:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                if signal is None:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                # Rovnaký M1 flip nesmie obchodovať dvakrát.
                if m1_candle_id == last_processed_m1:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                last_processed_m1 = m1_candle_id

                spec = await get_symbol_info(
                    connection,
                    symbol
                )

                if not spec:
                    await asyncio.sleep(LOOP_SECONDS)
                    continue

                point = float(
                    spec.get("point", 0.01)
                )

                digits = int(
                    spec.get("digits", 2)
                )

                async def get_order_prices():
                    price = await connection.get_symbol_price(
                        symbol
                    )

                    if signal == "BUY":
                        entry_price = float(price["ask"])

                        order_sl = round(
                            entry_price - SL_POINTS * point,
                            digits
                        )

                        order_tp = round(
                            entry_price + TP_POINTS * point,
                            digits
                        )

                    else:
                        entry_price = float(price["bid"])

                        order_sl = round(
                            entry_price + SL_POINTS * point,
                            digits
                        )

                        order_tp = round(
                            entry_price - TP_POINTS * point,
                            digits
                        )

                    return entry_price, order_sl, order_tp

                entry, sl, tp = await get_order_prices()

                # Maximálne 2 pokusy pri Invalid stops.
                for attempt in range(2):
                    try:
                        if attempt > 0:
                            await asyncio.sleep(1)
                            entry, sl, tp = (
                                await get_order_prices()
                            )

                        if signal == "BUY":
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

                        else:
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
                            f"{signal} OPENED:",
                            result
                        )

                        telegram(
                            f"{'🟢' if signal == 'BUY' else '🔴'} "
                            f"{signal} OTVORENÝ – TEST 1:1\n\n"
                            f"Symbol: {symbol}\n"
                            f"Lot: {LOT_SIZE}\n"
                            f"Cena: {entry}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n\n"
                            f"5M EMA50: {ema50_5m:.2f}\n"
                            f"5M PSAR: {psar_5m:.2f}\n"
                            f"1M PSAR: {current_psar_1m:.2f}\n\n"
                            f"BE: +{BE_TRIGGER:.0f} → "
                            f"+{BE_LOCK:.0f}\n"
                            "PSAR trailing: až po BE"
                        )

                        break

                    except Exception as error:
                        error_text = str(error)

                        print(
                            "Obchodná chyba:",
                            error_text
                        )

                        invalid_stops = (
                            "INVALID_STOPS" in error_text
                            or "Invalid stops" in error_text
                        )

                        if invalid_stops and attempt == 0:
                            print(
                                "Invalid stops - obnovujem cenu..."
                            )
                            continue

                        telegram(
                            f"❌ {signal} NEBOL OTVORENÝ\n\n"
                            f"Symbol: {symbol}\n"
                            f"Lot: {LOT_SIZE}\n"
                            f"Cena: {entry}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n\n"
                            f"CHYBA:\n{error}"
                        )

                        break

                await asyncio.sleep(LOOP_SECONDS)

            except Exception:
                error_text = traceback.format_exc()

                print(
                    "RIObot chyba v cykle:",
                    error_text
                )

                telegram(
                    "⚠️ RIObot chyba v cykle:\n\n"
                    f"{error_text}"
                )

                await asyncio.sleep(LOOP_SECONDS)

    except Exception:
        error_text = traceback.format_exc()

        print(
            "RIObot kritická chyba:",
            error_text
        )

        telegram(
            "❌ RIObot kritická chyba:\n\n"
