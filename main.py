import os
import time
import json
import urllib.request
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# 1. HTTP server pre Railway (drží port otvorený, aby kontajner nespadol)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
        
    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

# Spustenie servera na pozadí
threading.Thread(target=run_server, daemon=True).start()

# 2. Načítanie premenných z Railway
TOKEN = os.getenv('METAAPI_TOKEN', '')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', '')

print("Riobot štartuje: Lot 0.01, SL 7$, TP 6$, BE pri 2$")

# Hlavná slučka bota, aby kontajner neustále neštartoval a nevypínal sa
if __name__ == "__main__":
    while True:
        # Tu beží logika tvojho bota
        print("Bot kontroluje trh XAUUSD...")
        
        # Sem môžeš doplniť svoje volania na MetaApi
        
        time.sleep(60)
