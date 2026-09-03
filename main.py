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

# Parametre stratégie pre košík
LOT_TP1 = 0.30
COUNT_TP1 = 5
LOT_TP2 = 0.20
COUNT_TP2 = 3

# Risk manažment (upravený pre štandardný Demo účet s väčším priestorom)
SL_DISTANCE = 35.0  # Pevný Stop Loss 35$ od vstupnej ceny
TP_DISTANCE = 50.0  # Take Profit 50$ od vstupnej ceny
BE_TRIGGER_USD = 8.0  # Break-Even sa posunie pri zisku 8 USD


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


async def open_initial_positions(connection, direction="BUY"):
    """Otvorí košík pozícií s pevným SL a TP."""
    logger.info(f"Otváram nový obchodný košík pre {SYMBOL} ({direction})...")
    
    # Zistíme aktuálnu cenu trhu
    price = await connection.get_symbol_price(SYMBOL)
    current_price = price['ask'] if direction == "BUY" else price['bid']
    
    # Vypočítame SL a TP
    if direction == "BUY":
        stop_loss = current_price - SL_DISTANCE
        take_profit = current_price + TP_DISTANCE
    else:
        stop_loss = current_price + SL_DISTANCE
        take_profit = current_price - SL_DISTANCE

    try:
        # Otvorenie TP1 sady
        for _ in range(COUNT_TP1):
            if direction == "BUY":
                await connection.create_market_buy_order(
                    symbol=SYMBOL,
                    volume=LOT_TP1,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )
            else:
                await connection.create_market_sell_order(
                    symbol=SYMBOL,
                    volume=LOT_TP1,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )

        # Otvorenie TP2 sady
        for _ in range(COUNT_TP2):
            if direction == "BUY":
                await connection.create_market_buy_order(
                    symbol=SYMBOL,
                    volume=LOT_TP2,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )
            else:
                await connection.create_market_sell_order(
                    symbol=SYMBOL,
                    volume=LOT_TP2,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )

        logger.info("Košík úspešne otvorený s ochranou SL a TP.")
        await send_telegram_message(f"🟢 <b>{SYMBOL} Basket úspešne otvorený!</b>\nSmer: {direction}\nSL: {stop_loss:.2f} | TP: {take_profit:.2f}")
    except Exception as e:
        logger.error(f"Chyba pri otváraní pozícií: {e}")
        await send_telegram_message(f"❌ <b>Chyba pri otváraní obchodu:</b> {e}")


async def manage_open_positions(connection):
    """Sleduje pozície a posúva na Break-Even, ak je splnený zisk."""
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
            
            # Kontrola pre BUY pozície -> Break-Even
            if pos_type == 'POSITION_TYPE_BUY':
                if profit_usd >= BE_TRIGGER_USD and current_sl < open_price:
                    await connection.modify_position(
                        position_id=ticket,
                        stop_loss=open_price + 0.10,
                        take_profit=position.get('takeProfit', 0)
                    )
                    await send_telegram_message(
                        f"🛡️ <b>{SYMBOL} Break-Even aktivovaný!</b>\n"
                        f"Ticket: <code>{ticket}</code>\n"
                        f"Zisk dosiahol: <b>{profit_usd} USD</b>\n"
                        f"SL posunutý na zaistenie zisku."
                    )

            # Kontrola pre SELL pozície -> Break-Even
            elif pos_type == 'POSITION_TYPE_SELL':
                if profit_usd >= BE_TRIGGER_USD and (current_sl > open_price or current_sl == 0):
                    await connection.modify_position(
                        position_id=ticket,
                        stop_loss=open_price - 0.10,
                        take_profit=position.get('takeProfit', 0)
                    )
                    await send_telegram_message(
                        f"🛡️ <b>{SYMBOL} Break-Even aktivovaný (SELL)!</b>\n"
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

    logger.info("Skrypt pre riadenie XAUUSD basketu úspešne spustený a beží.")
    await send_telegram_message("🚀 <b>Riobot úspešne spustený!</b> Traja muškátéri sú v pohotovosti.")

    # Skontrolujeme, či už nejaké pozície bežia
    positions = await connection.get_positions()
    xauusd_positions = [p for p in positions if p['symbol'] == SYMBOL]

    if not xauusd_positions:
        await open_initial_positions(connection, direction="BUY")
    else:
        logger.info("Na účte už existujú otvorené pozície XAUUSD, preskakujem počiatočný vstup a začínam manažment.")

    while True:
        await manage_open_positions(connection)
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
