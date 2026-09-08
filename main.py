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
        print(f"Telegram error: {e}")

print("Riobot štartuje v čistom režime...")

async def bot_loop():
    metaapi = MetaApi(TOKEN)
    price_history = []
    
    while True:
        try:
            print("Pripájam sa k MetaApi účtu...")
            try:
                account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
                if account.state != 'DEPLOYED':
                    await account.deploy()
            except Exception as acc_e:
                print(f"Pozor (ignorované MetaApi varovanie): {acc_e}")
            
            # Priame pripojenie na účet bez zbytočného manažmentu subscription
            connection = metaapi.metatrader_account_api.get_client().get_rpc_connection(ACCOUNT_ID)
            await connection.connect()
            await connection.wait_synchronized()
            
            print("MetaApi pripojenie je stabilné a pripravené.")
            send_telegram("🚀 Riobot je pripojený a pripravený na obchody!")

            while True:
                try:
                    # 1. Manažment pozícií (Break-Even)
                    positions = await connection.get_positions()
                    for position in positions:
                        if position['symbol'] == 'XAUUSD':
                            profit = position['profit']
                            position_id = position['id']
                            open_price = position['openPrice']
                            current_sl = position.get('stopLoss', 0)
                            
                            if profit >= 2.0 and current_sl != 0:
                                if position['type'] == 'POSITION_TYPE_BUY' and current_sl < open_price:
                                    await connection.modify_position(position_id=position_id, stop_loss=open_price + 1.0, take_profit=position.get('takeProfit'))
                                    send_telegram(f"🛡️ Riobot: BUY v zisku {profit:.2f}$ -> SL na BE+1!")
                                elif position['type'] == 'POSITION_TYPE_SELL' and current_sl > open_price:
                                    await connection.modify_position(position_id=position_id, stop_loss=open_price - 1.0, take_profit=position.get('takeProfit'))
                                    send_telegram(f"🛡️ Riobot: SELL v zisku {profit:.2f}$ -> SL na BE-1!")

                    # 2. Sledovanie ceny
                    symbol_price = await connection.get_symbol_price('XAUUSD')
                    current_price = symbol_price['ask']
                    price_history.append(current_price)
                    if len(price_history) > 30:
                        price_history.pop(0)

                    print(f"XAUUSD Aktuálna cena: {current_price}")

                    # 3. Rýchle otvorenie obchodu hneď po zozbieraní 5 cien
                    if len(positions) == 0 and len(price_history) >= 5:
                        old_price = price_history[0]
                        
                        if current_price > old_price:
                            tp_price = current_price + 5.0
                            sl_price = current_price - 5.0
                            await connection.create_market_buy_order(symbol='XAUUSD', volume=0.01, stop_loss=sl_price, take_profit=tp_price, comment="Riobot Fast BUY")
                            send_telegram(f"🟢 Riobot otvoril BUY XAUUSD! Cena: {current_price}")
                        
                        elif current_price < old_price:
                            tp_price = current_price - 5.0
                            sl_price = current_price + 5.0
                            await connection.create_market_sell_order(symbol='XAUUSD', volume=0.01, stop_loss=sl_price, take_profit=tp_price, comment="Riobot Fast SELL")
                            send_telegram(f"🔴 Riobot otvoril SELL XAUUSD! Cena: {current_price}")

                except Exception as inner_e:
                    print(f"Chyba v obchodnom cykle: {inner_e}")
                    if "connection" in str(inner_e).lower() or "disconnected" in str(inner_e).lower():
                        raise inner_e

                await asyncio.sleep(20)

        except Exception as outer_e:
            print(f"Chyba pripojenia, opakujem o 10 sekúnd: {outer_e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(bot_loop())
