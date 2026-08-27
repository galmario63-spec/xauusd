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

# Telegram premenné prostredia
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAUUSD"

# Konfigurácia lotov a počtu obchodov
LOT_TP1 = 0.30
COUNT_TP1 = 5

LOT_TP2 = 0.20
COUNT_TP2 = 3

SL_POINTS = 350
TP1_POINTS = 400
TP2_POINTS = 800

# Parametre pre Break-Even (v USD na centovom účte)
BE_TRIGGER_USD = 3.00  # Keď zisk na obchode dosiahne 3 USD
BE_LOCK_USD = 1.00     # BE sa posunie na garantovaný zisk 1 USD

async def send_telegram_message(message):
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

async def open_initial_positions(connection):
    try:
        logger.info("Otváram nový obchodný basket pre XAUUSD...")
        
        # Otvorenie TP1 sady (5x 0.30 lot)
        for _ in range(COUNT_TP1):
            await connection.create_market_buy_order(
                symbol=SYMBOL,
                volume=LOT_TP1,
                stop_loss_points=SL_POINTS,
                take_profit_points=TP1_POINTS
            )
        
        # Otvorenie TP2 sady (3x 0.20 lot)
        for _ in range(COUNT_TP2):
            await connection.create_market_buy_order(
                symbol=SYMBOL,
                volume=LOT_TP2,
                stop_loss_points=SL_POINTS,
                take_profit_points=TP2_POINTS
            )
            
        logger.info("Basket úspešne otvorený.")
        await send_telegram_message("🟢 <b>XAUUSD Basket úspešne otvorený!</b> Všetky pozície naskočili do trhu.")
    except Exception as e:
        logger.error(f"Chyba pri otváraní pozícií: {e}")
        await send_telegram_message(f"❌ <b>Chyba pri otváraní obchodu:</b> {e}")

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
            
            # Kontrola pre BUY pozície
            if pos_type == 'POSITION_TYPE_BUY':
                if profit_usd >= BE_TRIGGER_USD and current_sl < open_price:
                    logger.info(f"Pozícia {ticket} dosiahla zisk {profit_usd} USD, posúvam SL na zaistenie zisku.")
                    await connection.modify_position(
                        position_id=ticket,
                        stop_loss=open_price + 0.10,
                        take_profit=position.get('takeProfit', 0)
                    )
                    # Odošli notifikáciu na Telegram
                    await send_telegram_message(
                        f"🛡️ <b>XAUUSD Break-Even aktivovaný!</b>\n"
                        f"Ticket: <code>{ticket}</code>\n"
                        f"Zisk dosiahol: <b>{profit_usd} USD</b>\n"
                        f"SL posunutý na zaistenie zisku."
                    )
                        
    except Exception as e:
        logger.error(f"Chyba pri manažmente pozícií (SL/BE): {e}")

async def main():
    if not ACCOUNT_ID or not TOKEN:
        logger.error("Chýbajú premenné prostredia METAAPI_ACCOUNT_ID alebo METAAPI_TOKEN.")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
        
    connection = account.get_rpc_connection()
    await connection.connect()

    logger.info("Skript pre riadenie XAUUSD basketu úspešne spustený a beží.")
    await send_telegram_message("🚀 <b>Riobot úspešne spustený!</b> Traja muškatéri sú v pohotovosti.")

    # Skontrolujeme, či už nejaké pozície bežia, ak nie, otvoríme nový basket
    positions = await connection.get_positions()
    xauusd_positions = [p for p in positions if p['symbol'] == SYMBOL]
    
    if not xauusd_positions:
        await open_initial_positions(connection)
    else:
        logger.info("Na účte už existujú otvorené pozície XAUUSD, preskakujem počiatočný vstup a začínam manažment.")

    while True:
        await manage_open_positions(connection)
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
