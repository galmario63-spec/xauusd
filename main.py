import asyncio
import logging
import os
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import aiohttp
from metaapi_cloud_sdk import MetaApi

# Nastavenie logovania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("btc_bot")

# Načítanie premenných prostredia
METAAPI_TOKEN = os.getenv('METAAPI_TOKEN')
METAAPI_ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = os.getenv("SYMBOL", "BTCUSD")
LOT_SIZE = float(os.getenv("LOT_SIZE", "0.01"))
SL_DISTANCE = float(os.getenv("SL_DISTANCE", "500"))
TP1_DISTANCE = float(os.getenv("TP1_DISTANCE", "600"))
TP2_DISTANCE = float(os.getenv("TP2_DISTANCE", "1200"))
BE_TRIGGER_USD = float(os.getenv("BE_TRIGGER_USD", "2.0"))
LOCKED_PROFIT_OFFSET = float(os.getenv("LOCKED_PROFIT_OFFSET", "50"))

# HTTP server kvôli Railway health checku
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"HTTP server bezi na porte {port}...")
    server.serve_forever()

def get_current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

async def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    logger.error(f"Chyba pri posielaní Telegram správy: {await response.text()}")
    except Exception as e:
        logger.error(f"Telegram exception: {e}")

async def open_basket_positions(connection, direction="BUY"):
    logger.info(f"Otváram 2-obchodný košík pre {SYMBOL} ({direction})...")
    try:
        price = await connection.get_symbol_price(SYMBOL)
        current_price = price['ask'] if direction == "BUY" else price['bid']
        open_time = get_current_time_str()

        if direction == "BUY":
            stop_loss = current_price - SL_DISTANCE
            tp1 = current_price + TP1_DISTANCE
            tp2 = current_price + TP2_DISTANCE
            await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)
            await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)
        else:
            stop_loss = current_price + SL_DISTANCE
            tp1 = current_price - TP1_DISTANCE
            tp2 = current_price - TP2_DISTANCE
            await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)
            await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)

        msg = (f"🔴 *{SYMBOL} {direction} — RIO_ENGINE*\n\n"
               f"Entry: `{current_price}`\n"
               f"TP1: `{tp1}`\n"
               f"TP2: `{tp2}`\n"
               f"SL: `{stop_loss}`\n"
               f"Time: `{open_time}`")
        await send_telegram_message(msg)
        logger.info(f"Košík úspešne otvorený a odoslaný na Telegram pre {SYMBOL}")
    except Exception as e:
        logger.error(f"Chyba pri otváraní pozícií: {e}")

async def manage_open_positions(connection):
    try:
        positions = await connection.get_positions()
        
        # Ak nie sú žiadne otvorené pozície pre tento symbol, otvoríme BUY košík (alebo môžeš zmeniť na SELL)
        if not any(p['symbol'] == SYMBOL for p in positions):
            logger.info(f"Žiadne otvorené pozície pre {SYMBOL}, otváram nový obchodný košík...")
            await open_basket_positions(connection, "BUY")

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

async def main():
    logger.info(f"Spustam MetaApi prepojenie pre {SYMBOL}...")
    
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        logger.critical("Chýbajú MetaApi premenné!")
        return

    metaapi = MetaApi(METAAPI_TOKEN)
    account = await metaapi.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    logger.info("MetaApi je pripojené a synchronizované!")

    while True:
        try:
            logger.info(f"Prebieha kontrola symbolu {SYMBOL}...")
            await manage_open_positions(connection)
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")
            traceback.print_exc()
        
        await asyncio.sleep(15)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()

    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kritická chyba pri štarte: {e}")
        traceback.print_exc()
