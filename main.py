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

print("Riobot štartuje v optimalizovanom režime...")

def calculate_ema(prices, period):
    if len(prices) < period:
        return prices[-1]
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_stochastic(prices, k_period=14):
    if len(prices) < k_period:
        return 50
    recent_prices = prices[-k_period:]
    lowest_low = min(recent_prices)
    highest_high = max(recent_prices)
    current_close = prices[-1]
    
    if highest_high == lowest_low:
        return 50
    return 100 * ((current_close - lowest_low) / (highest_high - lowest_low))

async def bot_loop():
    metaapi = MetaApi(TOKEN)
    price_history = []
    
    while True:
        try:
            print("Pripájam sa k MetaApi účtu...")
            account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
            
            if account.state != 'DEPLOYED':
                await account.deploy()
            
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
            
            print("MetaApi pripojenie stabilné. Bot sleduje trh.")
            send_telegram("🚀 Riobot je stabilne pripojený a stráži trh!")

            while True:
                try:
                    # 1. Manažment pozícií (BE na +2$ zisku)
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

                    # 2. Sledovanie trhu
                    symbol_price = await connection.get_symbol_price('XAUUSD')
                    current_price = symbol_price['ask']
                    price_history.append(current_price)
                    if len(price_history) > 200:
                        price_history.pop(0)

                    print(f"XAUUSD Aktuálna cena: {current_price}")

                    # 3. Stratégia: EMA + Fibo + Stochastic
                    if len(positions) == 0 and len(price_history) >= 100:
                        ema_50 = calculate_ema(price_history, 50)
                        ema_200 = calculate_ema(price_history, 100)

                        swing_high = max(price_history[-50:])
                        swing_low = min(price_history[-50:])
                        diff = swing_high - swing_low

                        fibo_50 = swing_high - (diff * 0.5)
                        fibo_618 = swing_high - (diff * 0.618)
                        stoch_k = calculate_stochastic(price_history)

                        if ema_50 > ema_200 and (fibo_618 <= current_price <= fibo_50) and stoch_k < 40:
                            tp_price = current_price + 6.0
                            sl_price = swing_low - 1.0

                            await connection.create_market_buy_order(
                                symbol='XAUUSD',
                                volume=0.01,
                                stop_loss=sl_price,
                                take_profit=tp_price,
                                comment="Riobot Fibo+Stoch"
                            )
                            send_telegram(f"🟢 Riobot otvoril BUY XAUUSD! Cena: {current_price}, TP: {tp_price}, SL: {sl_price}")

                except Exception as inner_e:
                    print(f"Chyba v obchodnom cykle: {inner_e}")
                    # Ak vypadne spojenie, vyskočíme von a obnovíme ho
                    if "connection" in str(inner_e).lower() or "disconnected" in str(inner_e).lower():
                        raise inner_e

                await asyncio.sleep(60)

        except Exception as outer_e:
            print(f"Chyba pripojenia, opätovný pokus o 15 sekúnd: {outer_e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(bot_loop())
