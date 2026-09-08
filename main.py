import subprocess
import sys

try:
    import metaapi_cloud_sdk
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "metaapi-cloud-sdk"])

import os
import time
import json
import asyncio
import urllib.request
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from metaapi_cloud_sdk import MetaApi

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

threading.Thread(target=run_server, daemon=True).start()

TOKEN = os.getenv('METAAPI_TOKEN', '')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', '')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Chyba pri posielaní Telegram správy: {e}")

print("Riobot štartuje: Stabilný režim")
send_telegram("🚀 Riobot bol úspešne spustený na Railway!")

async def bot_loop():
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    while True:
        try:
            # 1. Manažment otvorených pozícií (BE a TP kontrola)
            positions = await connection.get_positions()
            for position in positions:
                if position['symbol'] == 'XAUUSD':
                    profit = position['profit']
                    position_id = position['id']
                    open_price = position['openPrice']
                    current_sl = position.get('stopLoss', 0)
                    
                    if profit >= 2.0 and current_sl < open_price:
                        new_sl = open_price + 1.0
                        await connection.modify_position(
                            position_id=position_id,
                            stop_loss=new_sl,
                            take_profit=position.get('takeProfit')
                        )
                        send_telegram(f"🛡️ Riobot: XAUUSD v zisku {profit:.2f}$ -> SL posunutý na +1 BE!")

            # 2. Získanie aktuálnej ceny symbolu cez MT5 terminal state
            symbol_price = await connection.get_symbol_price('XAUUSD')
            current_price = symbol_price['ask']

            # Bezpečná kontrola a obchodná logika bez zložitých historických knižníc
            if len(positions) == 0:
                # Základná ochrana a test obchodu na základe aktuálnej ceny
                tp_price = current_price + 6.0
                sl_price = current_price - 3.0

                # Príklad vstupu pre overenie stability
                print((f"Aktuálna cena XAUUSD: {current_price}. Bot stabilne monitoruje trh."))

        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(bot_loop())
