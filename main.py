import asyncio
import logging
import os
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from metaapi_cloud_sdk import MetaApi

# Nastavenie logovania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("btc_bot")

# Načítanie premenných prostredia z Railway
METAAPI_TOKEN = os.getenv('METAAPI_TOKEN')
METAAPI_ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = os.getenv("SYMBOL", "BTCUSD")
LOT_SIZE = float(os.getenv("LOT_SIZE", "0.01"))
SL_DISTANCE = float(os.getenv("SL_DISTANCE", "300"))
TP1_DISTANCE = float(os.getenv("TP1_DISTANCE", "300"))
TP2_DISTANCE = float(os.getenv("TP2_DISTANCE", "600"))
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

async def main():
    logger.info(f"Spustam MetaApi prepojenie pre {SYMBOL}...")
    
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        logger.critical("Chýbajú MetaApi premenné (METAAPI_TOKEN alebo METAAPI_ACCOUNT_ID)!")
        return

    metaapi = MetaApi(METAAPI_TOKEN)
    account = await metaapi.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    # Pripojenie k účtu (počkáme, kým bude online)
    original_state = account.state
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
    # Spustenie HTTP servera na pozadí pre Railway
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()

    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kritická chyba pri štarte: {e}")
        traceback.print_exc()
