import asyncio
import logging
import os
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Konfigurácia logovania
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xauusd_bot")

SYMBOL = os.getenv("SYMBOL", "XAUUSD")
BE_TRIGGER_USD = float(os.getenv("BE_TRIGGER_USD", "10.0"))
LOCKED_PROFIT_OFFSET = float(os.getenv("LOCKED_PROFIT_OFFSET", "2.0"))

# --- HTTP SERVER PRE RAILWAY, ABY NEVYPÍNAL KONTAJNER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot je nažive!")

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Fiktívny HTTP server beží na porte {port}...")
    server.serve_forever()

# --------------------------------------------------------

async def manage_open_positions():
    try:
        logger.info(f"Prebieha kontrola symbolu {SYMBOL}...")
    except Exception as e:
        logger.error(f"Chyba pri správe pozícií: {e}")

async def main():
    logger.info("Spúšťam XAUUSD trading bot na Railway...")
    
    while True:
        try:
            logger.info("Bot beží a čaká na dáta...")
            await manage_open_positions()
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")
            traceback.print_exc()
        
        await asyncio.sleep(10)

if __name__ == "__main__":
    # Spustíme HTTP server na pozadí v samostatnom vlákne, aby Railway videl port
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()

    # Spustíme hlavnú asynchrónnu logiku bota
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kritická chyba pri štarte: {e}")
        traceback.print_exc()
