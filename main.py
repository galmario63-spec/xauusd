
import os
import json
import time
import asyncio
import requests

from datetime import datetime, timezone
from threading import Thread
from flask import Flask
from metaapi_cloud_sdk import MetaApi

# =====================================================
# RIOBOT GOLD ZONES M1 - FOUR TRADES
# =====================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 0.01
MAX_TRADES = 4

# Bezpecny test. Zmena cez Render Environment:
# ENABLE_TRADING=true zapne realne objednavky.
ENABLE_TRADING = (
    os.getenv("ENABLE_TRADING", "false").lower() == "true"
)

COMMENT = "RIO ZONES M1 TEST4"

LOOP_SECONDS = 5
RECONNECT_SECONDS = 3
META_TIMEOUT = 30
COOLDOWN_SECONDS = 180

STATE_FILE = "rio_zones_state.json"

# ZONY
ZONE_LOOKBACK = 30
ZONE_TOLERANCE = 0.35
MIN_TOUCHES = 2

# Potvrdenie odrazu
MIN_BODY_RATIO = 0.35
MIN_WICK_RATIO = 0.25

# ATR / RISK
ATR_PERIOD = 14
SL_ATR_BUFFER = 0.35
MIN_SL_DISTANCE = 0.40
MAX_SL_DISTANCE = 2.00
MAX_SPREAD = 0.40

# TP1 / TP OPEN
TP1_RR = 1.00
BE_TRIGGER_RR = 0.70
BE_LOCK_DISTANCE = 0.10

TRAIL_TRIGGER_RR = 1.50
TRAIL_ATR_MULTIPLIER = 1.00

# NEWS
NEWS_URL = (
    "https://nfs.faireconomy.media/"
    "ff_calendar_thisweek.json"
)

NEWS_BEFORE = 15
NEWS_AFTER = 30
NEWS_REFRESH = 1800
NEWS_MAX_AGE = 10800

# ENV
M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

# =====================================================
# RENDER
# =====================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIOBOT GOLD ZONES M1 ACTIVE", 200


def keep_alive():

    def server():
        app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", 10000))
        )

    Thread(target=server, daemon=True).start()


# =====================================================
# TELEGRAM
# =====================================================

def telegram(message):

    print(message, flush=True)

    if not T_TOKEN or not T_CHAT:
        return

    try:
        response = requests.post(
            f"https://api.telegram.org/bot"
            f"{T_TOKEN}/sendMessage",
            data={
                "chat_id": T_CHAT,
                "text": message
            },
            timeout=10
        )

        if response.status_code != 200:
            print(
                "TELEGRAM ERROR:",
                response.text[:200],
                flush=True
            )

    except Exception as e:
        print("TELEGRAM:", e, flush=True)


# =====================================================
# STATE / FOUR TRADES
# =====================================================

def default_state():

    return {
        "trade_count": 0,
        "last_signal": None,
        "last_trade_time": 0,
        "had_position": False,
        "trade_risk": None,
        "tp1_reached": False,
        "last_atr": None,
        "news": [],
        "news_fetch": 0,
        "halted": False,
        "order_uncertain": False
    }


def save_state(state):

    data = {
        "trade_count": state["trade_count"],
        "last_signal": state["last_signal"],
        "last_trade_time": state["last_trade_time"],
        "trade_risk": state["trade_risk"],
        "tp1_reached": state["tp1_reached"],
        "halted": state["halted"],
        "order_uncertain": state["order_uncertain"]
    }

    temp = STATE_FILE + ".tmp"

    with open(temp, "w") as f:
        json.dump(data, f)

    os.replace(temp, STATE_FILE)


def load_state():

    state = default_state()

    if os.path.exists(STATE_FILE):

        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)

            for key in saved:
                if key in state:
                    state[key] = saved[key]

        except Exception:
            # Nepovolime nove LIVE obchody
            # pri poskodenom pocitadle.
            state["halted"] = True

    return state


# =====================================================
# M1 CANDLES
# =====================================================

async def get_candles(region):

    url = (
        f"https://mt-market-data-client-api-v1."
        f"{region}.agiliumtrade.ai/"
        f"users/current/accounts/{M_ACC}/"
        f"historical-market-data/symbols/"
        f"{SYMBOL}/timeframes/1m/candles"
        f"?limit=100"
    )

    def fetch():

        r = requests.get(
            url,
            headers={"auth-token": M_TOKEN},
            timeout=25
        )

        r.raise_for_status()
        return r.json()

    raw = await asyncio.to_thread(fetch)

    if not isinstance(raw, list):
        raise RuntimeError("INVALID M1 DATA")

    candles = []

    now = datetime.now(timezone.utc)

    for c in raw:

        try:
            dt = datetime.fromisoformat(
                str(c["time"]).replace("Z", "+00:00")
            )

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            age = (now - dt).total_seconds()

            # Iba uzavrete M1 sviecky.
            if age < 65:
                continue

            candles.append({
                "time": dt.isoformat(),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"])
            })

        except (KeyError, ValueError, TypeError):
            continue

    candles.sort(key=lambda x: x["time"])

    return candles


# =====================================================
# ATR
# =====================================================

def calculate_atr(candles):

    if len(candles) < ATR_PERIOD + 2:
        return None

    values = []

    for i in range(1, len(candles)):

        c = candles[i]
        p = candles[i - 1]

        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"])
        )

        values.append(tr)

    return sum(values[-ATR_PERIOD:]) / ATR_PERIOD


# =====================================================
# BUY / SELL ZONES - NO PSAR
# =====================================================

def detect_zones(candles):

    history = candles[
        -(ZONE_LOOKBACK + 1):-1
    ]

    if len(history) < ZONE_LOOKBACK:
        return None, None

    lows = []
    highs = []

    for i in range(2, len(history) - 2):

        c = history[i]
        window = history[i - 2:i + 3]

        if c["low"] <= min(
            x["low"] for x in window
        ):
            lows.append(c["low"])

        if c["high"] >= max(
            x["high"] for x in window
        ):
            highs.append(c["high"])

    def valid(values):

        result = []

        for value in values:

            touches = sum(
                abs(v - value) <= ZONE_TOLERANCE
                for v in values
            )

            if touches >= MIN_TOUCHES:
                result.append(value)

        return result

    reference_price = candles[-2]["close"]

    supports = [
        x for x in valid(lows)
        if x < reference_price
    ]

    resistances = [
        x for x in valid(highs)
        if x > reference_price
    ]

    support = max(supports) if supports else None

    resistance = (
        min(resistances) if resistances else None
    )

    return support, resistance


# =====================================================
# CONFIRMED REJECTION
# =====================================================

def get_signal(candles, support, resistance):

    c = candles[-1]
    p = candles[-2]

    candle_range = c["high"] - c["low"]

    if candle_range <= 0:
        return None

    body = abs(c["close"] - c["open"])

    if body / candle_range < MIN_BODY_RATIO:
        return None

    lower_wick = (
        min(c["open"], c["close"]) - c["low"]
    )

    upper_wick = (
        c["high"] - max(c["open"], c["close"])
    )

    # BUY
    if support is not None:

        touched = (
            abs(c["low"] - support)
            <= ZONE_TOLERANCE
        )

        bullish = (
            c["close"] > c["open"]
            and c["close"] > p["close"]
            and c["close"] > support
        )

        wick = (
            lower_wick / candle_range
            >= MIN_WICK_RATIO
        )

        if touched and bullish and wick:
            return "BUY"

    # SELL
    if resistance is not None:

        touched = (
            abs(c["high"] - resistance)
            <= ZONE_TOLERANCE
        )

        bearish = (
            c["close"] < c["open"]
            and c["close"] < p["close"]
            and c["close"] < resistance
        )

        wick = (
            upper_wick / candle_range
            >= MIN_WICK_RATIO
        )

        if touched and bearish and wick:
            return "SELL"

    return None


# =====================================================
# NEWS FILTER
# =====================================================

def fetch_news():

    r = requests.get(NEWS_URL, timeout=15)
    r.raise_for_status()

    raw = r.json()

    if not isinstance(raw, list):
        raise RuntimeError("INVALID NEWS DATA")

    events = []

    for event in raw:

        country = str(
            event.get("country")
            or event.get("currency")
            or ""
        ).upper()

        impact = str(
            event.get("impact", "")
        ).lower()

        if country != "USD":
            continue

        if "high" not in impact and "red" not in impact:
            continue

        try:
            dt = datetime.fromisoformat(
                str(event["date"]).replace(
                    "Z", "+00:00"
                )
            )

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            events.append(dt.timestamp())

        except (KeyError, ValueError, TypeError):
            continue

    return events


async def news_blocked(state):

    now = time.time()

    if now - state["news_fetch"] >= NEWS_REFRESH:

        try:
            state["news"] = await asyncio.to_thread(
                fetch_news
            )

            state["news_fetch"] = now

        except Exception as e:
            print("NEWS ERROR:", e, flush=True)

    if now - state["news_fetch"] > NEWS_MAX_AGE:
        return True

    for event_time in state["news"]:

        if (
            event_time - NEWS_BEFORE * 60
            <= now <=
            event_time + NEWS_AFTER * 60
        ):
            return True

    return False


# =====================================================
# MARKET / POSITIONS
# =====================================================

async def get_market(connection):

    spec = await asyncio.wait_for(
        connection.get_symbol_specification(SYMBOL),
        timeout=META_TIMEOUT
    )

    price = await asyncio.wait_for(
        connection.get_symbol_price(SYMBOL),
        timeout=META_TIMEOUT
    )

    digits = int(spec.get("digits", 2))

    tick = float(
        spec.get("tickSize")
        or 10 ** (-digits)
    )

    return {
        "bid": float(price["bid"]),
        "ask": float(price["ask"]),
        "tick": tick,
        "digits": digits,
        "spec": spec
    }


def normalize(price, market):

    tick = market["tick"]

    return round(
        round(price / tick) * tick,
        market["digits"]
    )


async def get_positions(connection):

    data = await asyncio.wait_for(
        connection.get_positions(),
        timeout=META_TIMEOUT
    )

    return [
        p for p in data
        if str(p.get("symbol", "")).upper()
        == SYMBOL.upper()
    ]


# =====================================================
# INITIAL SL / TP1
# =====================================================

def get_levels(
    side, entry, support, resistance, atr, market
):

    buffer = max(
        atr * SL_ATR_BUFFER,
        market["tick"] * 5
    )

    if side == "BUY":

        sl = support - buffer
        risk = entry - sl
        tp1 = entry + risk * TP1_RR

    else:

        sl = resistance + buffer
        risk = sl - entry
        tp1 = entry - risk * TP1_RR

    if not (
        MIN_SL_DISTANCE <= risk <= MAX_SL_DISTANCE
    ):
        return None

    return {
        "sl": normalize(sl, market),
        "tp1": normalize(tp1, market),
        "risk": risk
    }


# =====================================================
# OPEN TRADE / LIMIT FOUR
# =====================================================

async def open_trade(
    connection,
    side,
    support,
    resistance,
    atr,
    state
):

    # Absolutny limit testovacej serie.
    if state["trade_count"] >= MAX_TRADES:
        return False

    if state["halted"] or state["order_uncertain"]:
        return False

    # Len jedna XAUUSD pozicia.
    existing = await get_positions(connection)

    if existing:
        return False

    market = await get_market(connection)

    spread = market["ask"] - market["bid"]

    if spread > MAX_SPREAD:
        print("HIGH SPREAD - NO ENTRY", flush=True)
        return False

    spec = market["spec"]

    min_volume = float(
        spec.get("minVolume") or 0.01
    )

    volume_step = float(
        spec.get("volumeStep") or 0.01
    )

    if LOT_SIZE < min_volume:
        telegram("LOT 0.01 NOT ALLOWED")
        return False

    steps = LOT_SIZE / volume_step

    if abs(steps - round(steps)) > 1e-7:
        telegram("INVALID VOLUME STEP")
        return False

    entry = (
        market["ask"] if side == "BUY"
        else market["bid"]
    )

    levels = get_levels(
        side,
        entry,
        support,
        resistance,
        atr,
        market
    )

    if levels is None:
        return False

    sl = levels["sl"]
    tp1 = levels["tp1"]

    number = state["trade_count"] + 1

    message = (
        f"RIO GOLD ZONES M1\n"
        f"TRADE {number}/{MAX_TRADES}\n"
        f"SIGNAL: {side}\n"
        f"LOT: {LOT_SIZE}\n"
        f"ENTRY: {entry}\n"
        f"SL: {sl}\n"
        f"TP1: {tp1}\n"
        f"TP OPEN: TRAILING"
    )

    # TEST MODE
    if not ENABLE_TRADING:

        telegram("TEST SIGNAL - NO ORDER\n" + message)

        return False

    # Pri neistom vysledku objednavky
    # nesmieme automaticky opakovat vstup.
    state["order_uncertain"] = True
    save_state(state)

    try:

        options = {"comment": COMMENT}

        if side == "BUY":

            await asyncio.wait_for(
                connection.create_market_buy_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    None,
                    options
                ),
                timeout=META_TIMEOUT
            )

        else:

            await asyncio.wait_for(
                connection.create_market_sell_order(
                    SYMBOL,
                    LOT_SIZE,
                    sl,
                    None,
                    options
                ),
                timeout=META_TIMEOUT
            )

        # Objednavka bola potvrdena.
        state["trade_count"] += 1

        state["trade_risk"] = levels["risk"]

        state["tp1_reached"] = False

        state["had_position"] = True

        state["order_uncertain"] = False

        if state["trade_count"] >= MAX_TRADES:
            state["halted"] = True

        save_state(state)

        telegram(
            "ORDER OK\n\n" + message
        )

        if state["trade_count"] >= MAX_TRADES:

            telegram(
                "RIOBOT: FOUR TRADES REACHED\n"
                "NEW ENTRIES STOPPED.\n"
                "OPEN POSITION PROTECTION ACTIVE."
            )

        return True

    except Exception as e:

        state["halted"] = True
        save_state(state)

        telegram(
            "ORDER RESULT UNCERTAIN\n"
            f"{type(e).__name__}: {e}\n"
            "NEW ENTRIES BLOCKED.\n"
            "CHECK MT5 HISTORY."
        )

        return False


# =====================================================
# BREAK EVEN / TP1 / TP OPEN
# =====================================================

async def protect_position(
    connection,
    position,
    state
):

    market = await get_market(connection)

    ptype = str(position.get("type", "")).upper()

    if ptype in ("POSITION_TYPE_BUY", "0"):
        side = "BUY"

    elif ptype in ("POSITION_TYPE_SELL", "1"):
        side = "SELL"

    else:
        return

    pid = position["id"]

    entry = float(position["openPrice"])

    current_sl = float(
        position.get("stopLoss") or 0
    )

    if current_sl <= 0:

        telegram("WARNING: POSITION WITHOUT SL")
        return

    direction = 1 if side == "BUY" else -1

    price = (
        market["bid"] if side == "BUY"
        else market["ask"]
    )

    profit_distance = (
        price - entry
    ) * direction

    risk = state["trade_risk"]

    if not risk or risk <= 0:

        inferred = (
            entry - current_sl
        ) * direction

        if inferred > 0:

            risk = inferred
            state["trade_risk"] = risk
            save_state(state)

        else:
            # Povodny risk po restarte nie je znamy.
            # Existujuci SL nechavame nedotknuty.
            return

    wanted_sl = current_sl

    # BE do maleho plusu.
    if profit_distance >= risk * BE_TRIGGER_RR:

        be = entry + direction * BE_LOCK_DISTANCE

        if side == "BUY":
            wanted_sl = max(wanted_sl, be)

        else:
            wanted_sl = min(wanted_sl, be)

    # TP1: zamknutie polovice povodneho risku.
    # Pri lote 0.01 poziciu nedelime.
    if profit_distance >= risk * TP1_RR:

        if not state["tp1_reached"]:

            state["tp1_reached"] = True
            save_state(state)

            telegram(
                "RIO GOLD TP1 REACHED\n"
                "TP OPEN CONTINUES"
            )

        lock = entry + direction * risk * 0.50

        if side == "BUY":
            wanted_sl = max(wanted_sl, lock)

        else:
            wanted_sl = min(wanted_sl, lock)

    # Dynamicky trailing.
    if profit_distance >= risk * TRAIL_TRIGGER_RR:

        atr = state["last_atr"]

        if atr and atr > 0:

            distance = atr * TRAIL_ATR_MULTIPLIER

            trail = price - direction * distance

            if side == "BUY":
                wanted_sl = max(wanted_sl, trail)

            else:
                wanted_sl = min(wanted_sl, trail)

    wanted_sl = normalize(wanted_sl, market)

    tick = market["tick"]

    # Nikdy nezhorsit SL.
    if side == "BUY":

        if wanted_sl <= current_sl + tick / 2:
            return

        if wanted_sl >= market["bid"] - tick:
            return

    else:

        if wanted_sl >= current_sl - tick / 2:
            return

        if wanted_sl <= market["ask"] + tick:
            return

    try:

        await asyncio.wait_for(
            connection.modify_position(
                pid,
                wanted_sl,
                position.get("takeProfit")
            ),
            timeout=META_TIMEOUT
        )

        telegram(
            f"RIO GOLD SL UPDATED\n"
            f"{side}\n"
            f"NEW SL: {wanted_sl}"
        )

    except Exception as e:

        print("SL ERROR:", e, flush=True)


# =====================================================
# BOT SESSION
# =====================================================

async def bot_session(state):

    api = MetaApi(M_TOKEN)

    account = await asyncio.wait_for(
        api.metatrader_account_api.get_account(M_ACC),
        timeout=META_TIMEOUT
    )

    region = getattr(account, "region", None) or "london"

    if str(account.state).upper() != "DEPLOYED":

        await asyncio.wait_for(
            account.deploy(),
            timeout=META_TIMEOUT
        )

    await asyncio.wait_for(
        account.wait_connected(),
        timeout=120
    )

    connection = account.get_rpc_connection()

    try:

        await asyncio.wait_for(
            connection.connect(),
            timeout=META_TIMEOUT
        )

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=120
        )

        telegram(
            "RIO GOLD ZONES CONNECTED\n"
            f"LOT: {LOT_SIZE}\n"
            f"TRADES: {state['trade_count']}/{MAX_TRADES}\n"
            f"LIVE: {ENABLE_TRADING}"
        )

        current = await get_positions(connection)

        state["had_position"] = bool(current)

        errors = 0

        while True:

            try:

                # Vzdy najprv ochrana pozicii.
                current = await get_positions(connection)

                if current:

                    state["had_position"] = True

                    for position in current:

                        await protect_position(
                            connection,
                            position,
                            state
                        )

                else:

                    if state["had_position"]:

                        state["had_position"] = False

                        state["last_trade_time"] = time.time()

                        state["trade_risk"] = None

                        state["tp1_reached"] = False

                        save_state(state)

                        telegram(
                            "POSITION CLOSED\n"
                            "COOLDOWN 180 SECONDS"
                        )

                    # Po 4 obchodoch stop.
                    if (
                        state["trade_count"] >= MAX_TRADES
                        or state["halted"]
                        or state["order_uncertain"]
                    ):

                        await asyncio.sleep(LOOP_SECONDS)
                        continue

                    # Cooldown.
                    if (
                        time.time() - state["last_trade_time"]
                        < COOLDOWN_SECONDS
                    ):

                        await asyncio.sleep(LOOP_SECONDS)
                        continue

                    # NEWS filter.
                    if await news_blocked(state):

                        await asyncio.sleep(LOOP_SECONDS)
                        continue

                    candles = await get_candles(region)

                    if len(candles) < ZONE_LOOKBACK + 2:

                        await asyncio.sleep(LOOP_SECONDS)
                        continue

                    atr = calculate_atr(candles)

                    if atr is None:

                        await asyncio.sleep(LOOP_SECONDS)
                        continue

                    state["last_atr"] = atr

                    support, resistance = detect_zones(
                        candles
                    )

                    side = get_signal(
                        candles,
                        support,
                        resistance
                    )

                    if side:

                        signal_key = [
                            candles[-1]["time"],
                            side
                        ]

                        if signal_key != state["last_signal"]:

                            state["last_signal"] = signal_key

                            save_state(state)

                            telegram(
                                f"ZONE SIGNAL: {side}\n"
                                f"SUPPORT: {support}\n"
                                f"RESISTANCE: {resistance}"
                            )

                            await open_trade(
                                connection,
                                side,
                                support,
                                resistance,
                                atr,
                                state
                            )

                errors = 0

                await asyncio.sleep(LOOP_SECONDS)

            except Exception as e:

                errors += 1

                print(
                    "LOOP ERROR:",
                    type(e).__name__,
                    e,
                    flush=True
                )

                if errors >= 3:
                    raise

                await asyncio.sleep(LOOP_SECONDS)

    finally:

        try:
            await connection.close()

        except Exception:
            pass


# =====================================================
# MAIN / RECONNECT
# =====================================================

async def main():

    keep_alive()

    if not M_TOKEN or not M_ACC:

        telegram("M_TOKEN OR M_ACC MISSING")
        return

    state = load_state()

    telegram(
        "RIOBOT GOLD ZONES M1 START\n"
        "NO PSAR\n"
        "LOT 0.01\n"
        f"MAX TRADES: {MAX_TRADES}\n"
        f"COUNT: {state['trade_count']}\n"
        f"LIVE TRADING: {ENABLE_TRADING}\n"
        "TP1 + TP OPEN + BE ACTIVE"
    )

    while True:

        try:

            await bot_session(state)

        except Exception as e:

            telegram(
                "RIOBOT CONNECTION ERROR\n"
                f"{type(e).__name__}: {e}\n"
                "RECONNECT IN 3 SECONDS"
            )

            await asyncio.sleep(RECONNECT_SECONDS)


# =====================================================
# START
# =====================================================

if __name__ == "__main__":
    asyncio.run(main())
