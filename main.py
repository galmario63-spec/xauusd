import os
import logging
import asyncio
from datetime import datetime
from metaapi_cloud_sdk import MetaApi

# Nastavenie logovania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Načítanie premenných prostredia
METAAPI_TOKEN = os.getenv('METAAPI_TOKEN')
METAAPI_ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "XAUUSD"
LOT_SIZE = 0.01
SL_DISTANCE = 300
TP1_DISTANCE = 300
TP2_DISTANCE = 600
BE_TRIGGER_USD = 2.0
LOCKED_PROFIT_OFFSET = 50

def is_allowed_trading_time():
    now = datetime.now()
    # Príklad: obchodné hodiny pondelok až piatok
    if now.weekday() >= 5:
        return False
    return True

def get_current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def check_smart_filter(candles, ema_short, ema_long, direction):
    try:
        # Posledná uzavretá sviečka
        last_candle = candles[-1]
        is_green = last_candle['close'] > last_candle['open']
        is_red = last_candle['close'] < last_candle['open']

        if direction == "BUY":
            # Podmienka pre BUY: Krátky priemer nad dlhým (rastúci trend) a zelená sviečka
            if ema_short >= ema_long and is_green:
                return True
        elif direction == "SELL":
            # Podmienka pre SELL: Krátky priemer pod dlhým (klesajúci trend) a červená sviečka
            if ema_short <= ema_long and is_red:
                return True

        return False
    except Exception as e:
        logger.error(f"Chyba pri vyhodnocovaní filtra: {e}")
        return True  # V prípade chyby prejdeme na istotu

async def open_basket_positions(connection, direction):
    if not is_allowed_trading_time():
        return

    # Overíme inteligentný filter vysokej úspešnosti
    # (Predpokladáme, že candles, ema_short a ema_long sú k dispozícii v kontexte)
    # filter_passed = await check_smart_filter(candles, ema_short, ema_long, direction)
    # if not filter_passed:
    #     logger.info(f"Filter pre {direction} zatiaľ nepotvrdil vstup, čakáme na ideálny moment.")
    #     return

    logger.info(f"Otváram 2-obchodný košík pre {SYMBOL} ({direction})...")

    try:
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
