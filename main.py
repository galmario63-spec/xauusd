import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import asyncio
import logging
import httpx
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TOKEN = os.getenv("METAAPI_TOKEN")

# Telegram údaje zadané priamo pre istotu, aby notifikácie hneď naskočili
TELEGRAM_BOT_TOKEN = "8767773639:AAFU_yeGuEDn_yeGuEDn_yeGuEDn"
TELEGRAM_CHAT_ID = "5357928157"

SYMBOL = "XAUUSD"

# Bezpečné minimálne loty pre testovanie
LOT_TP1 = 0.01
COUNT_TP1 = 3
LOT_TP2 = 0.01
COUNT_TP2 = 3
LOT_TP3 = 0.01
COUNT_TP3 = 2

SL_DISTANCE = 35.0
TP1_DISTANCE = 25.0
TP2_DISTANCE = 50.0
TP3_DISTANCE = 80.0
BE_TRIGGER_USD = 5.0


async def send_telegram_message(message: str):
    """Odošle notifikáciu do Telegram chatu."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                logger.info("✅ Telegram správa úspešne odoslaná!")
            else:
                logger.error(f"❌ Telegram API chybová odpoveď ({response.status_code}): {response.text}")
    except Exception as e:
        logger.error(f"❌ Výnimka pri odosielaní Telegram správy: {e}")


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
        logger.error("Chýbajú premenné prostredia pre MetaApi.")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()

    logger.info("Riobot bezpečný režim spustený.")
    
    # Okamžitý test Telegramu pri štarte
    await send_telegram_message("🚀 <b>Riobot hlási štart:</b> Prepojenie s Telegramom je úspešne nadviazané!")

    while True:
        try:
            await manage_open_positions(connection)
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")

        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
