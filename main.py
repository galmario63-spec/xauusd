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

# =========================================================
# PSAR
# =========================================================

PSAR_STEP = 0.02
PSAR_MAX = 0.20

# =========================================================
# EMA FILTER - 5M SMER
# =========================================================

EMA_PERIOD = 50

# =========================================================
# SL / TP V BODOCH
# =========================================================

TP_POINTS = 600.0
SL_POINTS = 1500.0

MAGIC = 26092026
COMMENT = "Riobot 5M trend 1M PSAR"

# =========================================================
# BREAK EVEN
# =========================================================

BE_TRIGGER = 250.0
BE_LOCK = 100.0

# =========================================================
# LOOP
# =========================================================

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
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):

    if not T_TOKEN or not T_CHAT:
        print(message)
        return

    try:
        url = (
            f"https://api.telegram.org/"
            f"bot{T_TOKEN}/sendMessage"
        )

        requests.post(
            url,
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )

    except Exception as e:
        print(
            "Telegram chyba:",
            e
        )


# =========================================================
# PSAR
# =========================================================

def calculate_psar(
    df,
    step=0.02,
    max_af=0.20
):

    high = (
        df["high"]
        .astype(float)
        .tolist()
    )

    low = (
        df["low"]
        .astype(float)
        .tolist()
    )

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

        # =================================================
        # BULL
        # =================================================

        if bull:

            psar[i] = (
                previous_psar
                + af * (
                    ep - previous_psar
                )
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

        # =================================================
        # BEAR
        # =================================================

        else:

            psar[i] = (
                previous_psar
                + af * (
                    ep - previous_psar
                )
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
# EMA
# =========================================================

def calculate_ema(
    df,
    period=50
):

    return (
        df["close"]
        .ewm(
            span=period,
            adjust=False
        )
        .mean()
    )


# =========================================================
# ERROR
# =========================================================

def get_error_details(
    api,
    error
):

    try:
        return api.format_error(
            error
        )

    except Exception:
        return str(error)


# =========================================================
# SYMBOL INFO
# =========================================================

async def get_symbol_info(
    connection,
    symbol
):

    try:
        return await (
            connection
            .get_symbol_specification(
                symbol
            )
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

async def get_positions(
    connection,
    symbol
):

    try:

        positions = (
            await connection
            .get_positions()
        )

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
# NAČÍTANIE SVIEČOK
# =========================================================

async def get_closed_candles(
    account,
    symbol,
    timeframe,
    limit=100,
    minimum=10
):

    try:

        candles = await (
            account.get_historical_candles(
                symbol=symbol,
                timeframe=timeframe,
                start_time=None,
                limit=limit
            )
        )

        if not candles:
            return None

        df = pd.DataFrame(
            candles
        )

        if df.empty:
            return None

        if "time" in df.columns:

            df = df.sort_values(
                "time"
            )

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
            return None

        for col in required:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna(
            subset=required
        )

        if len(df) < minimum + 1:
            return None

        # =============================================
        # POSLEDNÁ SVIEČKA SA EŠTE TVORÍ
        # =============================================

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

async def manage_break_even(
    connection,
    symbol
):

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

        for position in positions:

            try:

                position_id = position.get(
                    "id"
                )

                position_type = position.get(
                    "type"
                )

                open_price = float(
                    position.get(
                        "openPrice"
                    )
                )

                current_price = float(
                    position.get(
                        "currentPrice"
                    )
                )

                current_sl = position.get(
                    "stopLoss"
                )

                take_profit = position.get(
                    "takeProfit"
                )

                # =========================================
                # BUY
                # =========================================

                if (
                    position_type
                    == "POSITION_TYPE_BUY"
                ):

                    profit_points = (
                        current_price
                        - open_price
                    ) / point

                    if (
                        profit_points
                        >= BE_TRIGGER
                    ):

                        new_sl = round(
                            open_price
                            + BE_LOCK * point,
                            digits
                        )

                        if (
                            current_sl is None
                            or float(current_sl) < new_sl
                        ):

                            await (
                                connection
                                .modify_position(
                                    position_id=position_id,
                                    stop_loss=new_sl,
                                    take_profit=take_profit
                                )
                            )

                            telegram(
                                "🟢 BREAK EVEN – BUY\n\n"
                                f"Symbol: {symbol}\n"
                                f"Open: {open_price}\n"
                                f"Nový SL: {new_sl}\n"
                                f"Profit: {profit_points:.0f} bodov"
                            )

                # =========================================
                # SELL
                # =========================================

                elif (
                    position_type
                    == "POSITION_TYPE_SELL"
                ):

                    profit_points = (
                        open_price
                        - current_price
                    ) / point

                    if (
                        profit_points
                        >= BE_TRIGGER
                    ):

                        new_sl = round(
                            open_price
                            - BE_LOCK * point,
                            digits
                        )

                        if (
                            current_sl is None
                            or float(current_sl) > new_sl
                        ):

                            await (
                                connection
                                .modify_position(
                                    position_id=position_id,
                                    stop_loss=new_sl,
                                    take_profit=take_profit
                                )
                            )

                            telegram(
                                "🔴 BREAK EVEN – SELL\n\n"
                                f"Symbol: {symbol}\n"
                                f"Open: {open_price}\n"
                                f"Nový SL: {new_sl}\n"
                                f"Profit: {profit_points:.0f} bodov"
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
# PSAR TRAILING STOP - 5M
# =========================================================

async def manage_psar_stop(
    connection,
    account,
    symbol
):

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

        min_stop_raw = spec.get(
            "minStopDistance"
        )

        try:
            min_stop_points = float(
                min_stop_raw
            )
        except Exception:
            min_stop_points = 0.0

        min_distance = (
            min_stop_points * point
        )

        # =============================================
        # PSAR TRAILING ZOSTÁVA NA 5M
        # =============================================

        closed_df = await get_closed_candles(
            account,
            symbol,
            "5m",
            limit=100,
            minimum=10
        )

        if closed_df is None:
            return

        psar, bullish = calculate_psar(
            closed_df,
            PSAR_STEP,
            PSAR_MAX
        )

        if psar is None:
            return

        psar = float(
            psar
        )

        price = await (
            connection.get_symbol_price(
                symbol
            )
        )

        bid = float(
            price["bid"]
        )

        ask = float(
            price["ask"]
        )

        for position in positions:

            try:

                position_id = position.get(
                    "id"
                )

                position_type = position.get(
                    "type"
                )

                current_sl = position.get(
                    "stopLoss"
                )

                take_profit = position.get(
                    "takeProfit"
                )

                # =========================================
                # BUY
                # =========================================

                if (
                    position_type
                    == "POSITION_TYPE_BUY"
                ):

                    candidate_sl = round(
                        psar,
                        digits
                    )

                    if candidate_sl >= bid:
                        continue

                    if min_distance > 0:

                        if (
                            bid - candidate_sl
                            < min_distance
                        ):
                            continue

                    if current_sl is not None:

                        if (
                            candidate_sl
                            <= float(current_sl)
                        ):
                            continue

                    await (
                        connection
                        .modify_position(
                            position_id=position_id,
                            stop_loss=candidate_sl,
                            take_profit=take_profit
                        )
                    )

                    telegram(
                        "📈 PSAR SL POSUN – BUY\n\n"
                        f"Symbol: {symbol}\n"
                        f"5M PSAR: {candidate_sl}\n"
                        f"Nový SL: {candidate_sl}\n"
                        f"Bid: {bid}"
                    )

                # =========================================
                # SELL
                # =========================================

                elif (
                    position_type
                    == "POSITION_TYPE_SELL"
                ):

                    candidate_sl = round(
                        psar,
                        digits
                    )

                    if candidate_sl <= ask:
                        continue

                    if min_distance > 0:

                        if (
                            candidate_sl - ask
                            < min_distance
                        ):
                            continue

                    if current_sl is not None:

                        if (
                            candidate_sl
                            >= float(current_sl)
                        ):
                            continue

                    await (
                        connection
                        .modify_position(
                            position_id=position_id,
                            stop_loss=candidate_sl,
                            take_profit=take_profit
                        )
                    )

                    telegram(
                        "📉 PSAR SL POSUN – SELL\n\n"
                        f"Symbol: {symbol}\n"
                        f"5M PSAR: {candidate_sl}\n"
                        f"Nový SL: {candidate_sl}\n"
                        f"Ask: {ask}"
                    )

            except Exception:

                print(
                    "PSAR SL chyba:",
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

        telegram(
            "❌ Chýba M_TOKEN"
        )

        return

    if not M_ACC:

        telegram(
            "❌ Chýba M_ACC"
        )

        return

    api = MetaApi(
        M_TOKEN
    )

    connection = None

    try:

        # =================================================
        # METAAPI
        # =================================================

        account = await (
            api.metatrader_account_api
            .get_account(
                M_ACC
            )
        )

        await account.wait_connected()

        connection = (
            account.get_rpc_connection()
        )

        await connection.connect()

        await connection.wait_synchronized()

        telegram(
            "🟢 RIObot spustený\n\n"
            f"Symbol: {SYMBOL_REQUEST}\n"
            f"Lot: {LOT_SIZE}\n"
            "Smer: 5M PSAR + EMA50\n"
            "Vstup: 1M PSAR FLIP\n"
            f"TP: {TP_POINTS} bodov\n"
            f"Počiatočný SL: {SL_POINTS} bodov\n"
            f"BE trigger: {BE_TRIGGER} bodov\n"
            f"BE lock: {BE_LOCK} bodov\n\n"
            "5M EMA50 FILTER: ON\n"
            "5M PSAR FILTER: ON\n"
            "1M PSAR FLIP: ON\n"
            "PSAR TRAILING SL: ON\n"
            "clientId: VYPNUTÝ"
        )

        # Posledná už spracovaná M1 sviečka.
        # Bráni opakovanému vstupu z rovnakého flipu.
        last_processed_m1 = None

        while True:

            try:

                symbol = SYMBOL_REQUEST

                # =================================================
                # SPRÁVA OTVORENEJ POZÍCIE
                # =================================================

                await manage_psar_stop(
                    connection,
                    account,
                    symbol
                )

                await manage_break_even(
                    connection,
                    symbol
                )

                # =================================================
                # 5M DÁTA - URČUJÚ SMER
                # =================================================

                df_5m = await get_closed_candles(
                    account,
                    symbol,
                    "5m",
                    limit=100,
                    minimum=55
                )

                # =================================================
                # 1M DÁTA - URČUJÚ VSTUP
                # =================================================

                df_1m = await get_closed_candles(
                    account,
                    symbol,
                    "1m",
                    limit=100,
                    minimum=20
                )

                if (
                    df_5m is None
                    or df_1m is None
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================================
                # 5M EMA50
                # =================================================

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

                # =================================================
                # 5M PSAR - TREND FILTER
                # =================================================

                psar_5m, bull_5m = calculate_psar(
                    df_5m,
                    PSAR_STEP,
                    PSAR_MAX
                )

                if psar_5m is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                psar_5m = float(
                    psar_5m
                )

                # BUY smer:
                # cena nad EMA50 + PSAR bullish
                trend_buy = (
                    bull_5m is True
                    and close_5m > ema50_5m
                )

                # SELL smer:
                # cena pod EMA50 + PSAR bearish
                trend_sell = (
                    bull_5m is False
                    and close_5m < ema50_5m
                )

                # =================================================
                # 1M PSAR FLIP
                # =================================================

                previous_1m = (
                    df_1m.iloc[:-1].copy()
                )

                current_1m = (
                    df_1m.copy()
                )

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

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

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

                # =================================================
                # FINÁLNY SIGNÁL
                # =================================================

                if (
                    trend_buy
                    and psar_flip_buy_1m
                ):
                    signal = "BUY"

                elif (
                    trend_sell
                    and psar_flip_sell_1m
                ):
                    signal = "SELL"

                else:
                    signal = None

                # =================================================
                # ID POSLEDNEJ UZAVRETEJ M1 SVIEČKY
                # =================================================

                if "time" in df_1m.columns:

                    m1_candle_id = str(
                        df_1m["time"].iloc[-1]
                    )

                else:

                    m1_candle_id = str(
                        df_1m.index[-1]
                    )

                print(
                    "\n================================"
                )

                print(
                    f"[5M TREND] {symbol}"
                )

                print(
                    f"5M Close: {close_5m}"
                )

                print(
                    f"5M EMA50: {ema50_5m}"
                )

                print(
                    f"5M PSAR: {psar_5m}"
                )

                print(
                    f"5M Bullish: {bull_5m}"
                )

                print(
                    f"BUY trend: {trend_buy}"
                )

                print(
                    f"SELL trend: {trend_sell}"
                )

                print(
                    "--------------------------------"
                )

                print(
                    "[1M ENTRY]"
                )

                print(
                    f"1M PSAR: {current_psar_1m}"
                )

                print(
                    f"1M Bullish: {current_bull_1m}"
                )

                print(
                    f"1M BUY flip: {psar_flip_buy_1m}"
                )

                print(
                    f"1M SELL flip: {psar_flip_sell_1m}"
                )

                print(
                    f"FINAL SIGNAL: {signal}"
                )

                print(
                    "================================"
                )

                # =================================================
                # IBA JEDNA POZÍCIA
                # =================================================

                positions = await get_positions(
                    connection,
                    symbol
                )

                if positions:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================================
                # BEZ SIGNÁLU
                # =================================================

                if signal is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================================
                # NEOBCHODUJ TEN ISTÝ M1 FLIP DVAKRÁT
                # =================================================

                if (
                    m1_candle_id
                    == last_processed_m1
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # Označíme flip ako spracovaný.
                # Aj keby order zlyhal, robot ho nebude
                # každých 10 sekúnd naháňať.
                last_processed_m1 = m1_candle_id

                # =================================================
                # SYMBOL PARAMETRE
                # =================================================

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

                # =================================================
                # ČERSTVÁ CENA PRE OBJEDNÁVKU
                # =================================================

                async def get_order_prices():

                    current_price = await (
                        connection
                        .get_symbol_price(
                            symbol
                        )
                    )

                    if signal == "BUY":

                        entry_price = float(
                            current_price["ask"]
                        )

                        order_sl = round(
                            entry_price
                            - SL_POINTS * point,
                            digits
                        )

                        order_tp = round(
                            entry_price
                            + TP_POINTS * point,
                            digits
                        )

                    else:

                        entry_price = float(
                            current_price["bid"]
                        )

                        order_sl = round(
                            entry_price
                            + SL_POINTS * point,
                            digits
                        )

                        order_tp = round(
                            entry_price
                            - TP_POINTS * point,
                            digits
                        )

                    return (
                        entry_price,
                        order_sl,
                        order_tp
                    )

                entry, sl, tp = (
                    await get_order_prices()
                )

                print(
                    "\n========== ORDER =========="
                )

                print(
                    f"Signal: {signal}"
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
                    f"5M EMA50: {ema50_5m}"
                )

                print(
                    f"5M PSAR: {psar_5m}"
                )

                print(
                    f"1M PSAR: {current_psar_1m}"
                )

                print(
                    "===========================\n"
                )

                # =================================================
                # MAX 2 POKUSY
                # =================================================

                for attempt in range(2):

                    try:

                        if attempt > 0:

                            await asyncio.sleep(1)

                            (
                                entry,
                                sl,
                                tp
                            ) = await get_order_prices()

                            print(
                                "🔄 Nová cena - retry objednávky"
                            )

                        # =========================================
                        # BUY
                        # =========================================

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
                                f"TP: {tp}\n\n"
                                f"5M EMA50: {ema50_5m:.2f}\n"
                                f"5M PSAR: {psar_5m:.2f}\n"
                                f"1M PSAR: {current_psar_1m:.2f}\n\n"
                                "5M trend: BUY ✅\n"
                                "1M PSAR flip: BUY ✅\n"
                                "BE: ON\n"
                                "PSAR trailing: ON"
                            )

                            break

                        # =========================================
                        # SELL
                        # =========================================

                        elif signal == "SELL":

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
                                f"TP: {tp}\n\n"
                                f"5M EMA50: {ema50_5m:.2f}\n"
                                f"5M PSAR: {psar_5m:.2f}\n"
                                f"1M PSAR: {current_psar_1m:.2f}\n\n"
                                "5M trend: SELL ✅\n"
                                "1M PSAR flip: SELL ✅\n"
                                "BE: ON\n"
                                "PSAR trailing: ON"
                            )

                            break

                    except Exception as error:

                        error_text = str(
                            error
                        )

                        print(
                            "Obchodná chyba:",
                            error_text
                        )

                        # =========================================
                        # RETRY INVALID STOPS
                        # =========================================

                        if (
                            "INVALID_STOPS"
                            in error_text
                            or
                            "Invalid stops"
                            in error_text
                        ):

                            if attempt == 0:

                                print(
                                    "⚠️ Invalid stops - "
                                    "obnovujem cenu..."
                                )

                                continue

                        detailed_error = (
                            get_error_details(
                                api,
                                error
                            )
                        )

                        telegram(
                            f"❌ {signal} NEBOL OTVORENÝ\n\n"
                            f"Symbol: {symbol}\n"
                            f"Lot: {LOT_SIZE}\n"
                            f"Cena: {entry}\n"
                            f"SL: {sl}\n"
                            f"TP: {tp}\n\n"
                            f"CHYBA:\n"
                            f"{error}\n\n"
                            f"DETAIL:\n"
                            f"{detailed_error}\n\n"
                            f"Point: {point}\n"
                            f"Digits: {digits}\n"
                            f"Min lot: {min_volume}\n"
                            f"Max lot: {max_volume}\n"
                            f"Lot step: {volume_step}\n"
                            f"Min stop: {min_stop}"
                        )

                        break

                await asyncio.sleep(
                    LOOP_SECONDS
                )

            except Exception:

                error_text = (
                    traceback.format_exc()
                )

                print(
                    "⚠️ RIObot chyba v cykle:"
                )

                print(
                    error_text
                )

                telegram(
                    "⚠️ RIObot chyba v cykle:\n\n"
                    f"{error_text}"
                )

                await asyncio.sleep(
                    LOOP_SECONDS
                )

    except Exception:

        error_text = (
            traceback.format_exc()
        )

        print(
            "❌ RIObot kritická chyba:"
        )

        print(
            error_text
        )

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

    asyncio.run(
        main()
)
