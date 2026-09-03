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

# Parametre pre 3-úrovňový košík
LOT_TP1 = 0.30
COUNT_TP1 = 3
LOT_TP2 = 0.30
COUNT_TP2 = 3
LOT_TP3 = 0.20
COUNT_TP3 = 2

# Risk manažment pre štandardný Demo účet
SL_DISTANCE = 35.0      # Pevný Stop Loss
TP1_DISTANCE = 25.0     # Take Profit pre 1. sadu
TP2_DISTANCE = 50.0     # Take Profit pre 2. sadu
TP3_DISTANCE = 80.0     # Take Profit pre 3. sadu
BE_TRIGGER_USD = 8.0    # Break-Even pri zisku 8 USD


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
    """Otvorí košík pozícií rozdelený na 3 rôzne TP úrovne."""
    logger.info(f"Otváram 3-úrovňový košík pre {SYMBOL} v smere {direction}...")
    
    price = await connection.get_symbol_price(SYMBOL)
    current_price = price['ask'] if direction == "BUY" else price['bid']
    
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
        # 1. Sada pre TP1
        for _ in range(COUNT_TP1):
            if direction == "BUY":
                await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_TP1, stop_loss=stop_loss, take_profit=tp1)
            else:
                await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_TP1, stop_loss=stop_loss, take_profit=tp1)

        # 2. Sada pre TP2
        for _ in range(COUNT_TP2):
            if direction == "BUY":
                await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_TP2, stop_loss=stop_loss, take_profit=tp2)
            else:
                await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_TP2, stop_loss=stop_loss, take_profit=tp2)

        # 3. Sada pre TP3
        for _ in range(COUNT_TP3):
            if direction == "BUY":
                await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_TP3, stop_loss=stop_loss, take_profit=tp3)
            else:
                await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_TP3, stop_loss=stop_loss, take_profit=tp3)

        logger.info(f"Košík s 3 TP úrovňami ({direction}) úspešne otvorený.")
        await send_telegram_message(
            f"🟢 <b>{SYMBOL} 3-TP Basket otvorený ({direction})!</b>\n"
            f"🛡️ SL: {stop_loss:.2f}\n"
            f"🎯 TP1 ({COUNT_TP1}x): {tp1:.2f}\n"
            f"🎯 TP2 ({COUNT_TP2}x): {tp2:.2f}\n"
            f"🎯 TP3 ({COUNT_TP3}x): {tp3:.2f}"
        )
    except Exception as e:
        logger.error(f"Chyba pri otváraní 3-TP košíka: {e}")
        await send_telegram_message(f"❌ <b>Chyba pri obchode:</b> {e}")


async def manage_open_positions(connection):
    """Sleduje pozície a posúva Stop Loss na Break-Even pri dosiahnutí zisku."""
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

    logger.info("Riobot s 3-TP stratégiou úspešne spustený.")
    await send_telegram_message("🚀 <b>Riobot s 3-TP stratégią úspešne spustený a online!</b>")

    # Skontrolujeme, či už nejaké pozície bežia
    positions = await connection.get_positions()
    xauusd_positions = [p for p in positions if p['symbol'] == SYMBOL]

    if not xauusd_positions:
        # Ak nič nebeží, otvoríme stabilný košík (predvolene BUY)
        await open_basket_positions(connection, direction="BUY")
    else:
        logger.info("Na účte už existujú otvorené pozície XAUUSD, preskakujem vstup a spúšťam manažment.")

    while True:
        try:
            await manage_open_positions(connection)
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")

        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main))
