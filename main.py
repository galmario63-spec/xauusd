import asyncio
import logging
import os
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("btc_bot")

SYMBOL = os.getenv("SYMBOL", "BTCUSD")
BE_TRIGGER_USD = float(os.getenv("BE_TRIGGER_USD", "50.0"))
LOCKED_PROFIT_OFFSET = float(os.getenv("LOCKED_PROFIT_OFFSET", "10.0"))

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
        logger.info(f"Prebieha kontrola symbolu {SYMBOL}...")
    except Exception as e:
        logger.error(f"Chyba pri sprave pozicii: {e}")

async def main():
    logger.info("Spustam BTC trading bot na Railway (obchodovanie do 21:00)...")
    
    while True:
        try:
            current_hour = datetime.now().hour
            
            # Obchodujeme len do 21:00 (hodina < 21)
            if current_hour < 21:
                logger.info("Bot bezi a obchoduje...")
                await manage_open_positions()
            else:
                logger.info(f"Je po 21:00 (aktualne {datetime.now().strftime('%H:%M')}), obchodovanie je pozastavene do zajtra.")
                
        except Exception as e:
            logger.error(f"Chyba v hlavnej slucke: {e}")
            traceback.print_exc()
        
        await asyncio.sleep(30)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()

    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kriticka chyba pri starte: {e}")
        traceback.print_exc()
