import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Načítanie premenných prostredia
ACCOUNT_ID = os.getenv("META_API_ACCOUNT_ID")
TOKEN = os.getenv("METAA_TOKEN") # Nechaj tak, alebo prepni na METAAPI_TOKEN podla Railway

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAUUSD"

# Parametre pre obchody (0.01 lotu)
LOT_SIZE = 0.01

SL_DISTANCE = 15.0
TP1_DISTANCE = 6.0  # TP1 na 6 USD zisku
TP2_DISTANCE = 9.0  # TP2 na 9 USD zisku

# Bezpečný Break-Even / Zámok zisku
BE_TRIGGER_USD = 6.0
LOCKED_PROFIT_OFFSET = 2.0  # Keď zisk dosiahne 6 $, SL sa posunie

START_HOUR = 8
END_HOUR = 21

NEXT_DIRECTION = "BUY"

def get_current_time_str():
    return datetime.now(ZoneInfo("Europe/Bratislava")).strftime("%Y-%m-%d %H:%M:%S")

def is_allowed_trading_time():
    now = datetime.now(ZoneInfo("Europe/Bratislava"))
    current_hour = now.hour
    if now.weekday() >= 5: # Víkendový filter
        return False
    return START_HOUR <= current_hour < END_HOUR

async def check_smart_filter(connection, direction):
    """Inteligentný filter: EMA trend + potvrdenie poslednej sviečky M5"""
    try:
        # Načítame posledné sviečky z M5
        candles = await connection.get_historical_candles(SYMBOL, "M5", 5)
        if not candles or len(candles) < 5:
            return True # Ak nie sú dáta, pustíme obchod

        # Jednoduchý výpočet priemeru (EMA alternatíva zo 5 sviečok)
        closes = [c['close'] for c in candles]
        ema_short = sum(closes[-3:]) / 3
        ema_long = sum(closes) / len(closes)

        # Posledná uzavretá sviečka
        last_candle = candles[-1]
        is_green = last_candle['close'] > last_candle['open']
        is_red = last_candle['close'] < last_candle['open']

        if direction == "BUY":
            # Podmienka pre BUY: Krátky priemer nad dlhým (rastúci trend) a sviečka musí byť zelená
            if ema_short >= ema_long and is_green:
                return True
        elif direction == "SELL":
            # Podmienka pre SELL: Krátky priemer pod dlhým (klesajúci trend) a sviečka musí byť červená
            if ema_short <= ema_long and is_red:
                return True

        return False
    except Exception as e:
        logger.error(f"Chyba pri vyhodnocovaní filtra: {e}")
        return True # V prípade chyby v API prejdeme na istotu

async def open_basket_positions(connection, direction):
    if not is_allowed_trading_time():
        return

    # Overíme inteligentný filter vysokej úspešnosti
    filter_passed = await check_smart_filter(connection, direction)
    if not filter_passed:
        logger.info(f"Filter pre {direction} zatiaľ nepotvrdil vstup, čakáme na ďalšiu sviečku.")
        return

    logger.info(f"Otváram 2-obchodný košík pre {SYMBOL} ({direction})...")

    price = await connection.get_symbol_price(SYMBOL)
    current_price = price['ask'] if direction == "BUY" else price['bid']
    open_time = get_current_time_str()

    if direction == "BUY":
        stop_loss = current_price - SL_DISTANCE
        tp1 = current_price + TP1_DISTANCE
        tp2 = current_price + TP2_DISTANCE
    else:
        stop_loss = current_price + SL_DISTANCE
        tp1 = current_price - TP1_DISTANCE
        tp2 = current_price - TP2_DISTANCE

    try:
        # TP1 (1 obchod)
        if direction == "BUY":
            await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)
        else:
            await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)

        # TP2 (1 obchod)
        if direction == "BUY":
            await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)
        else:
            await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)

        logger.info(f"Košík úspešne otvorený pre {SYMBOL}")
    except Exception as e:
        logger.error(f"Chyba pri otváraní pozícií: {e}")

async def manage_open_positions(connection):
    try:
        positions = await connection.get_positions()
        for position in positions:
            if position['symbol'] != SYMBOL:
                continue

            ticket = position['id']
            open_price = position['openPrice']
            profit_usd = position.get('profit', 0.0)
            current_sl = position.get('stopLoss', 0.0)
            pos_type = position['type']

            # Zámok zisku (Break-Even)
            if profit_usd >= BE_TRIGGER_USD:
                if pos_type == "POSITION_TYPE_BUY" and current_sl < open_price + LOCKED_PROFIT_OFFSET:
                    new_sl = open_price + LOCKED_PROFIT_OFFSET
                    await connection.modify_position(ticket, stop_loss=new_sl, take_profit=position.get('takeProfit'))
                    logger.info(f"Posunutý SL do zisku pre BUY pozíciu #{ticket}")
                elif pos_type == "POSITION_TYPE_SELL" and (current_sl > open_price - LOCKED_PROFIT_OFFSET or current_sl == 0):
                    new_sl = open_price - LOCKED_PROFIT_OFFSET
                    await connection.modify_position(ticket, stop_loss=new_sl, take_profit=position.get('takeProfit'))
                    logger.info(f"Posunutý SL do zisku pre SELL pozíciu #{ticket}")

    except Exception as e:
        logger.error(f"Chyba pri správe otvorených pozícií: {e}")
