import asyncio
import logging
import os
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("btc_bot")

# Konfigurácia z premenných prostredia Railway
SYMBOL = os.getenv("SYMBOL", "BTCUSD")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN", "")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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

async def manage_open_positions():
    try:
        logger.info(f"Prebieha kontrola symbolu {SYMBOL} cez MetaApi...")
        # Sem príde reálna logika pre MetaApi (načítanie pozícií, vyhodnotenie a odoslanie na Telegram)
        
    except Exception as e:
        logger.error(f"Chyba pri sprave pozicii: {e}")

async def main():
    logger.info("Spustam kompletny BTC trading bot na Railway...")
    
    while True:
        try:
            logger.info("Bot bezi a analyzuje trh...")
            await manage_open_positions()
        except Exception as e:
            logger.error(f"Chyba v hlavnej slucke: {e}")
            traceback.print_exc()
        
        await asyncio.sleep(15)

if __name__ == "__main__":
    # Spustenie HTTP servera na pozadí kvôli Railway health checku
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()

    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kriticka chyba pri starte: {e}")
        traceback.print_exc()
