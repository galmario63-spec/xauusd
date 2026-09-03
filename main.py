import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import asyncio
import logging
import httpx
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Konfigurácia účtu a Telegramu z premenných prostredia v Railway
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TOKEN = os.getenv("METAAPI_TOKEN")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAUUSD"

# Bezpečné minimálne loty pre testovanie (0.01)
LOT_TP1 = 0.01
COUNT_TP1 = 3
LOT_TP2 = 0.01
COUNT_TP2 = 3
LOT_TP3 = 0.01
COUNT_TP3 = 2

# Risk manažment pre bezpečné testovanie
SL_DISTANCE = 35.0
TP1_DISTANCE = 25.0
TP2_DISTANCE = 50.0
TP3_DISTANCE = 80.0
BE_TRIGGER_USD = 5.0


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


async def manage_open_positions(connection):
    """Sleduje existujúce pozície a posúva Stop Loss na Break-Even."""
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
            
            # Break-Even pre BUY
            if pos_type == 'POSITION_TYPE_BUY' and profit_usd >= BE_TRIGGER_USD and current_sl < open_price:
                await connection.modify_position(
                    position_id=ticket,
                    stop_loss=open_price + 0.10,
                    take_profit=position.get('takeProfit', 0)
                )
                await send_telegram_message(
                    f"🛡️ <b>{SYMBOL} Break-Even aktivovaný!</b>\n"
                    f"Ticket: <code>{ticket}</code> | Zisk: <b>{profit_usd:.2f} USD</b>"
                )

            # Break-Even pre SELL
            elif pos_type == 'POSITION_TYPE_SELL' and profit_usd >= BE_TRIGGER_USD and (current_sl > open_price or current_sl == 0):
                await connection.modify_position(
                    position_id=ticket,
                    stop_loss=open_price - 0.10,
                    take_profit=position.get('takeProfit', 0)
                )
                await send_telegram_message(
                    f"🛡️ <b>{SYMBOL} Break-Even aktivovaný (SELL)!</b>\n"
                    f"Ticket: <code>{ticket}</code> | Zisk: <b>{profit_usd:.2f} USD</b>"
                )
                    
    except Exception as e:
        logger.error(f"Chyba pri manažmente BE: {e}")


async def main():
    if not ACCOUNT_ID or not TOKEN:
        logger.error("Chýbajú premenné prostredia.")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()

    logger.info("Riobot bezpečný režim spustený.")
    await send_telegram_message("🚀 <b>Riobot je online v bezpečnom režime (0.01 lotu)!</b> Sleduje účet.")

    while True:
        try:
            # Bot teraz len manažuje pozície, nič neotvára naslepo
            await manage_open_positions(connection)
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")

        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
