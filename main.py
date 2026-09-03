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

# Nastavenia pre centový účet / test: 3 úrovne TP, na každú 2 obchody (spolu 6)
LOT_SIZE = 0.01
COUNT_PER_TP = 2 

# Kompaktné vzdialenosti pre XAUUSD
SL_DISTANCE = 15.0
TP1_DISTANCE = 3.0
TP2_DISTANCE = 6.0
TP3_DISTANCE = 10.0
BE_TRIGGER_USD = 2.0

# Zvýšená rezerva pre Break-Even na pokrytie poplatków a spreadu (namiesto +0.10 dáme väčší odskok)
BE_OFFSET = 0.35 

# Časový manažment (Bratislava / SELČ)
START_HOUR = 8
END_HOUR = 21


def get_current_time_str():
    """Vráti aktuálny čas v našej zóne vo formáte HH:MM."""
    return datetime.now(ZoneInfo("Europe/Bratislava")).strftime("%H:%M")


def is_allowed_trading_time():
    """Skontroluje, či je aktuálny čas v povolenom obchodnom okne a či nie je víkend."""
    now = datetime.now(ZoneInfo("Europe/Bratislava"))
    current_hour = now.hour
    if now.weekday() >= 5: # Sobota, Nedelľa
        return False
    return START_HOUR <= current_hour < END_HOUR


async def send_telegram_message(message: str):
    """Odošle notifikáciu do Telegram chatu."""
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
        logger.error(f"Chyba pri odosielaní Telegram správy: {e}")


async def open_basket_positions(connection, direction="BUY"):
    """Otvorí košík pre BUY alebo SELL s kompaktnými TP."""
    if not is_allowed_trading_time():
        logger.info("Mimo povoleného obchodného času – nový košík sa neotvára.")
        return

    logger.info(f"Otváram kompaktný 3-TP košík pre {SYMBOL} ({direction})...")
    
    price = await connection.get_symbol_price(SYMBOL)
    current_price = price['ask'] if direction == "BUY" else price['bid']
    open_time = get_current_time_str()
    
    if direction == "BUY":
        stop_loss = current_price - SL_DISTANCE
        tp1 = current_price + TP1_DISTANCE
        tp2 = current_price + TP2_DISTANCE
        tp3 = current_price + TP3_DISTANCE
    else:
        stop_loss = current_price + SL_DISTANCE
        tp1 = current_price - TP1_DISTANCE
        tp2 = current_price - TP2_DISTANCE
        tp3 = current_price - TP3_DISTANCE

    try:
        # 1. TP1 (2 obchody)
        for _ in range(COUNT_PER_TP):
            if direction == "BUY":
                await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)
            else:
                await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)

        # 2. TP2 (2 obchody)
        for _ in range(COUNT_PER_TP):
            if direction == "BUY":
                await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)
            else:
                await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)

        # 3. TP3 (2 obchody)
        for _ in range(COUNT_PER_TP):
            if direction == "BUY":
                await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp3)
            else:
                await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp3)

        logger.info(f"Košík úspešne otvorený v smere {direction}.")
        
        # Telegram notifikácia
        await send_telegram_message(
            f"🚨 <b>{SYMBOL} {direction} — RIO_ENGINE</b>\n\n"
            f"Entry: <code>{current_price:.2f}</code>\n"
            f"TP1 (2x): <code>{tp1:.2f}</code>\n"
            f"TP2 (2x): <code>{tp2:.2f}</code>\n"
            f"TP3 (2x): <code>{tp3:.2f}</code>\n"
            f"SL: <code>{stop_loss:.2f}</code>\n"
            f"Time: <b>{open_time}</b>\n"
            f"Engine: <b>2.5.0 (Optimized BE)</b>"
        )
    except Exception as e:
        logger.error(f"Chyba pri otváraní košíka: {e}")
        await send_telegram_message(f"❌ <b>Chyba pri obchode:</b> {e}")


async def manage_open_positions(connection):
    """Sleduje pozície a posúva Stop Loss na Break-Even s bezpečnou rezervou pre poplatky."""
    try:
        positions = await connection.get_positions()
        current_time = get_current_time_str()
        
        for position in positions:
            if position['symbol'] != SYMBOL:
                continue
                
            ticket = position['id']
            open_price = position['openPrice']
            profit_usd = position.get('profit', 0)
            current_sl = position.get('stopLoss', 0)
            pos_type = position['type']
            
            if pos_type == 'POSITION_TYPE_BUY' and profit_usd >= BE_TRIGGER_USD and current_sl < open_price:
                new_sl = open_price + BE_OFFSET
                await connection.modify_position(
                    position_id=ticket,
                    stop_loss=new_sl,
                    take_profit=position.get('takeProfit', 0)
                )
                await send_telegram_message(
                    f"🛡️ <b>{SYMBOL} Break-Even aktivovaný!</b>\n"
                    f"Ticket: <code>{ticket}</code> | Zisk: <b>{profit_usd:.2f} USD</b>\n"
                    f"Time: <b>{current_time}</b>"
                )

            elif pos_type == 'POSITION_TYPE_SELL' and profit_usd >= BE_TRIGGER_USD and (current_sl > open_price or current_sl == 0):
                new_sl = open_price - BE_OFFSET
                await connection.modify_position(
                    position_id=ticket,
                    stop_loss=new_sl,
                    take_profit=position.get('takeProfit', 0)
                )
                await send_telegram_message(
                    f"🛡️ <b>{SYMBOL} Break-Even aktivovaný (SELL)!</b>\n"
                    f"Ticket: <code>{ticket}</code> | Zisk: <b>{profit_usd:.2f} USD</b>\n"
                    f"Time: <b>{current_time}</b>"
                )
                    
    except Exception as e:
        logger.error(f"Chyba pri manažmente pozícií: {e}")


async def main():
    if not ACCOUNT_ID or not TOKEN:
        logger.error("Chýbajú premenné prostredia pre MetaApi.")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()

    logger.info("Riobot 2.5 spustený.")
    startup_time = get_current_time_str()
    await send_telegram_message(f"🚀 <b>Riobot Engine 2.5 spustený!</b> (Čas: {startup_time})")

    while True:
        try:
            positions = await connection.get_positions()
            xauusd_positions = [p for p in positions if p['symbol'] == SYMBOL]

            if not xauusd_positions and is_allowed_trading_time():
                await open_basket_positions(connection, direction="BUY")

            await manage_open_positions(connection)

        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")

        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
