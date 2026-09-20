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
# EMA FILTER
# =========================================================

EMA_PERIOD = 50

# =========================================================
# SL / TP V BODOCH
# =========================================================

TP_POINTS = 600.0
SL_POINTS = 1500.0

MAGIC = 26092026
COMMENT = "Riobot PSAR EMA50"

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
                    ep
                    - previous_psar
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
                    ep
                    - previous_psar
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
                            or float(current_sl)
                            < new_sl
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
                                f"Profit: "
                                f"{profit_points:.0f} bodov"
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
                            or float(current_sl)
                            > new_sl
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
# PSAR TRAILING STOP
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

        # =================================================
        # 5M CANDLES
        # =================================================

        candles = await (
            account.get_historical_candles(
                symbol=symbol,
                timeframe="5m",
                start_time=None,
                limit=100
            )
        )

        if not candles:

            return

        df = pd.DataFrame(
            candles
        )

        if df.empty:

            return

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

            return

        for col in required:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna(
            subset=required
        )

        if len(df) < 10:

            return

        # =================================================
        # ODSTRÁNIME AKTUÁLNU TVORIACU SA SVIEČKU
        # =================================================

        closed_df = df.iloc[:-1].copy()

        if len(closed_df) < 10:

            return

        psar, bullish = calculate_psar(
            closed_df,
            PSAR_STEP,
            PSAR_MAX
        )

        if psar is None:

            return

        psar = float(psar)

        # =================================================
        # AKTUÁLNA CENA
        # =================================================

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

        # =================================================
        # POZÍCIE
        # =================================================

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

                # =================================================
                # BUY
                # =================================================

                if (
                    position_type
                    == "POSITION_TYPE_BUY"
                ):

                    candidate_sl = round(
                        psar,
                        digits
                    )

                    # PSAR musí byť pod cenou
                    if candidate_sl >= bid:

                        continue

                    # minimálna vzdialenosť
                    if min_distance > 0:

                        if (
                            bid - candidate_sl
                            < min_distance
                        ):

                            continue

                    # SL môže ísť iba vyššie
                    if current_sl is not None:

                        current_sl_float = float(
                            current_sl
                        )

                        if (
                            candidate_sl
                            <= current_sl_float
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
                        f"PSAR: {candidate_sl}\n"
                        f"Nový SL: {candidate_sl}\n"
                        f"Bid: {bid}"
                    )

                    print(
                        f"[PSAR SL] BUY -> "
                        f"{candidate_sl}"
                    )

                # =================================================
                # SELL
                # =================================================

                elif (
                    position_type
                    == "POSITION_TYPE_SELL"
                ):

                    candidate_sl = round(
                        psar,
                        digits
                    )

                    # PSAR musí byť nad cenou
                    if candidate_sl <= ask:

                        continue

                    # minimálna vzdialenosť
                    if min_distance > 0:

                        if (
                            candidate_sl - ask
                            < min_distance
                        ):

                            continue

                    # SL môže ísť iba nižšie
                    if current_sl is not None:

                        current_sl_float = float(
                            current_sl
                        )

                        if (
                            candidate_sl
                            >= current_sl_float
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
                        f"PSAR: {candidate_sl}\n"
                        f"Nový SL: {candidate_sl}\n"
                        f"Ask: {ask}"
                    )

                    print(
                        f"[PSAR SL] SELL -> "
                        f"{candidate_sl}"
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
# NAČÍTANIE 5M DÁT
# =========================================================

async def get_market_data(
    account,
    symbol
):

    candles = await (
        account.get_historical_candles(
            symbol=symbol,
            timeframe="5m",
            start_time=None,
            limit=100
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

    if len(df) < 60:

        return None

    # =================================================
    # IBA UZAVRETÉ SVIEČKY
    # =================================================

    closed_df = df.iloc[:-1].copy()

    if len(closed_df) < 55:

        return None

    return closed_df


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
            f"Timeframe: 5M\n"
            f"TP: {TP_POINTS} bodov\n"
            f"Počiatočný SL: {SL_POINTS} bodov\n"
            f"BE trigger: {BE_TRIGGER} bodov\n"
            f"BE lock: {BE_LOCK} bodov\n\n"
            "PSAR: ON\n"
            "PSAR TRAILING SL: ON\n"
            "EMA50 FILTER: ON\n"
            "Vstup: PSAR FLIP + EMA50\n"
            "clientId: VYPNUTÝ"
        )

        last_signal = None

        # =================================================
        # HLAVNÝ LOOP
        # =================================================

        while True:

            try:

                symbol = SYMBOL_REQUEST

                # =================================================
                # PSAR TRAILING
                # =================================================

                await manage_psar_stop(
                    connection,
                    account,
                    symbol
                )

                # =================================================
                # BREAK EVEN
                # =================================================

                await manage_break_even(
                    connection,
                    symbol
                )

                # =================================================
                # MARKET DATA 5M
                # =================================================

                df = await get_market_data(
                    account,
                    symbol
                )

                if df is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================================
                # PSAR FLIP
                # =================================================

                previous_df = df.iloc[:-1].copy()

                current_df = df.copy()

                previous_psar, previous_bull = (
                    calculate_psar(
                        previous_df,
                        PSAR_STEP,
                        PSAR_MAX
                    )
                )

                current_psar, current_bull = (
                    calculate_psar(
                        current_df,
                        PSAR_STEP,
                        PSAR_MAX
                    )
                )

                if (
                    previous_psar is None
                    or current_psar is None
                ):

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================================
                # EMA50
                # =================================================

                ema_series = calculate_ema(
                    df,
                    EMA_PERIOD
                )

                ema50 = float(
                    ema_series.iloc[-1]
                )

                close_price = float(
                    df["close"].iloc[-1]
                )

                current_psar = float(
                    current_psar
                )

                # =================================================
                # PSAR FLIP
                # =================================================

                psar_flip_buy = (
                    previous_bull is False
                    and current_bull is True
                )

                psar_flip_sell = (
                    previous_bull is True
                    and current_bull is False
                )

                # =================================================
                # EMA FILTER
                # =================================================

                buy_allowed = (
                    psar_flip_buy
                    and close_price > ema50
                )

                sell_allowed = (
                    psar_flip_sell
                    and close_price < ema50
                )

                if buy_allowed:

                    signal = "BUY"

                elif sell_allowed:

                    signal = "SELL"

                else:

                    signal = None

                print(
                    "\n=============================="
                )

                print(
                    f"[5M] {symbol}"
                )

                print(
                    f"Close: {close_price}"
                )

                print(
                    f"PSAR: {current_psar}"
                )

                print(
                    f"PSAR bullish: {current_bull}"
                )

                print(
                    f"EMA50: {ema50}"
                )

                print(
                    f"PSAR flip BUY: "
                    f"{psar_flip_buy}"
                )

                print(
                    f"PSAR flip SELL: "
                    f"{psar_flip_sell}"
                )

                print(
                    f"Signal: {signal}"
                )

                print(
                    "=============================="
                )

                # =================================================
                # IBA JEDNA POZÍCIA
                # =================================================

                positions = await get_positions(
                    connection,
                    symbol
                )

                if positions:

                    print(
                        "[INFO] Pozícia už existuje."
                    )

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================================
                # ŽIADNY VALIDNÝ SIGNÁL
                # =================================================

                if signal is None:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

                # =================================================
                # OCHRANA PROTI DUPLIKÁTU
                # =================================================

                if signal == last_signal:

                    await asyncio.sleep(
                        LOOP_SECONDS
                    )

                    continue

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
                # FUNKCIA PRE ČERSTVÚ CENU
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

                # =================================================
                # PRVÝ POKUS
                # =================================================

                entry, sl, tp = (
                    await get_order_prices()
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
                    f"PSAR: {current_psar}"
                )

                print(
                    f"EMA50: {ema50}"
                )

                print(
                    "Timeframe: 5M"
                )

                print(
                    "PSAR FLIP: ON"
                )

                print(
                    "EMA50 FILTER: ON"
                )

                print(
                    "PSAR TRAILING: ON"
                )

                print(
                    "clientId: VYPNUTÝ"
                )

                print(
                    "================================\n"
                )

                order_opened = False

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
                            ) = await (
                                get_order_prices()
                            )

                            print(
                                "🔄 NOVÁ CENA – "
                                "OPAKUJEM OBJEDNÁVKU"
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
                                f"TP: {tp}\n"
                                f"PSAR: {current_psar}\n"
                                f"EMA50: {ema50}\n"
                                "Timeframe: 5M\n"
                                "PSAR flip: ÁNO\n"
                                "EMA50 filter: ÁNO\n"
                                "PSAR trailing: ON"
                            )

                            order_opened = True

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
                                f"TP: {tp}\n"
                                f"PSAR: {current_psar}\n"
                                f"EMA50: {ema50}\n"
                                "Timeframe: 5M\n"
                                "PSAR flip: ÁNO\n"
                                "EMA50 filter: ÁNO\n"
                                "PSAR trailing: ON"
                            )

                            order_opened = True

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
                        # RETRY PRI INVALID STOPS
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
                                    "⚠️ Invalid stops – "
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

                # =================================================
                # SIGNAL SA ZAPÍŠE IBA PO ÚSPEŠNOM OBCHODE
                # =================================================

                if order_opened:

                    last_signal = signal

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
