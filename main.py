import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import asyncio
import logging
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TOKEN = os.getenv("METAAPI_TOKEN")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAUUSD"

# Parametre pre obchody (0.01 lotu)
LOT_SIZE = 0.01

SL_DISTANCE = 15.0
TP1_DISTANCE = 6.0   # TP1 na 6 USD zisku
TP2_DISTANCE = 9.0   # TP2 na 9 USD zisku

# Bezpečný Break-Even / Zámok zisku
BE_TRIGGER_USD = 6.0 
LOCKED_PROFIT_OFFSET = 2.0 # Keď zisk dosiahne 6 $, SL sa posunie do plusu +2 $

START_HOUR = 8
END_HOUR = 21

NEXT_DIRECTION = "BUY"


def get_current_time_str():
    return datetime.now(ZoneInfo("Europe/Bratislava")).strftime("%H:%M")


def is_allowed_trading_time():
    now = datetime.now(ZoneInfo("Europe/Bratislava"))
    current_hour = now.hour
    if now.weekday() >= 5: # Víkendový filter
        return False
    return START_HOUR <= current_hour < END_HOUR


async def send_telegram_message(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=10.0)
    except Exception as e:
        logger.error(f"Chyba pri Telegram správe: {e}")


async def check_smart_filter(connection, direction):
    """Inteligentný filter: EMA trend + potvrdenie poslednej sviečky na M5 (Cieľ: 85-90% úspešnosť)"""
    try:
        # Načítame posledné sviečky z M5
        candles = await connection.get_historical_candles(SYMBOL, "M5", 10)
        if not candles or len(candles) < 5:
            return True # Ak nie sú dáta, pustíme obchod

        # Jednoduchý výpočet priemeru (EMA alternatíva zo 5 sviečok) pre trend
        closes = [c['close'] for c in candles]
        ema_short = sum(closes[-3:]) / 3
        ema_long = sum(closes) / len(closes)

        # Posledná uzavretá sviečka
        last_candle = candles[-1]
        is_green = last_candle['close'] > last_candle['open']
        is_red = last_candle['close'] < last_candle['open']

        if direction == "BUY":
            # Podmienka pre BUY: Krátky priemer nad dlhým (rastúci trend) + posledná sviečka musí byť zelená
            if ema_short >= ema_long and is_green:
                return True
        elif direction == "SELL":
            # Podmienka pre SELL: Krátky priemer pod dlhým (klesajúci trend) + posledná sviečka musí byť červená
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
        logger.info(f"Filter pre {direction} zatiaľ nepotvrdil vstup, čakáme na ideálnu sviečku...")
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

        await send_telegram_message(
            f"🚨 <b>{SYMBOL} {direction} — RIO_ENGINE (2.9 Smart)</b>\n\n"
            f"Entry: <code>{current_price:.2f}</code>\n"
            f"TP1 (6$): <code>{tp1:.2f}</code>\n"
            f"TP2 (9$): <code>{tp2:.2f}</code>\n"
            f"SL: <code>{stop_loss:.2f}</code>\n"
            f"Time: <b>{open_time}</b>"
        )
    except Exception as e:
        logger.error(f"Chyba pri otváraní: {e}")


async def manage_open_positions(connection):
    try:
        positions = await connection.get_positions()
        
        for position in positions:
            if position['symbol'] != SYMBOL:
                continue
                
            ticket = position['id']
            open_price = position['openPrice']
            profit_usd = position.get('profit', 0)
            current_sl = position.get('stopLoss', 0)
            pos_type = position['type']
            
            # Ak zisk dosiahne 6 USD, zamkneme SL do istého plusu (+2 USD)
            if pos_type == 'POSITION_TYPE_BUY' and profit_usd >= BE_TRIGGER_USD and current_sl < (open_price + LOCKED_PROFIT_OFFSET):
                new_sl = open_price + LOCKED_PROFIT_OFFSET
                await connection.modify_position(position_id=ticket, stop_loss=new_sl, take_profit=position.get('takeProfit', 0))
                await send_telegram_message(f"🛡️ <b>{SYMBOL} Zisk zamknutý (+2$ na SL)!</b>\nTicket: <code>{ticket}</code> | Zisk: <b>{profit_usd:.2f} USD</b>")

            elif pos_type == 'POSITION_TYPE_SELL' and profit_usd >= BE_TRIGGER_USD and (current_sl > (open_price - LOCKED_PROFIT_OFFSET) or current_sl == 0):
                new_sl = open_price - LOCKED_PROFIT_OFFSET
                await connection.modify_position(position_id=ticket, stop_loss=new_sl, take_profit=position.get('takeProfit', 0))
                await send_telegram_message(f"🛡️ <b>{SYMBOL} Zisk zamknutý (SELL +2$ na SL)!</b>\nTicket: <code>{ticket}</code> | Zisk: <b>{profit_usd:.2f} USD</b>")
                    
    except Exception as e:
        logger.error(f"Chyba pri manažmente: {e}")


async def main():
    global NEXT_DIRECTION
    if not ACCOUNT_ID or not TOKEN:
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()

    logger.info("Riobot 2.9 spustený.")
    await send_telegram_message(f"🚀 <b>Riobot Engine 2.9 (Smart Filter) spustený!</b>")

    while True:
        try:
            positions = await connection.get_positions()
            xauusd_positions = [p for p in positions if p['symbol'] == SYMBOL]

            if not xauusd_positions and is_allowed_trading_time():
                await open_basket_positions(connection, direction=NEXT_DIRECTION)
                # Po otvorení prepneme smer pre ďalší košík (BUY <-> SELL)
                # (Smer sa prepne len ak filter pustil obchod, prípadne môžeme nechať takto)

            await manage_open_positions(connection)

        except Exception as e:
            logger.error(f"Chyba v slučke: {e}")

        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
