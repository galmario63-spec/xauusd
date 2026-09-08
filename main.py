import subprocess
import sys

# Automatická inštalácia knižnice pri štarte, ak chýba
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

# 1. HTTP server pre Railway (drží port otvorený)
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

# 2. Načítanie premenných
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

print("Riobot štartuje: 1h Fibo + EMA stratégia, Lot 0.01, TP 6$, BE pri 2$")
send_telegram("🚀 Riobot so 1h Fibo stratégiou bol úspešne spustený na Railway!")

# Výpočet EMA
def calculate_ema(prices, period):
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

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
            # 1. Manažment existujúcich pozícií (BE a TP kontrola)
            positions = await connection.get_positions()
            for position in positions:
                if position['symbol'] == 'XAUUSD':
                    profit = position['profit']
                    position_id = position['id']
                    open_price = position['openPrice']
                    current_sl = position.get('stopLoss', 0)
                    
                    # Ak je zisk >= 2 USD a SL ešte nie je na BE (+1 USD v cene/zisku)
                    if profit >= 2.0 and current_sl < open_price:
                        new_sl = open_price + 1.0  # Posun na +1 BE pre BUY
                        await connection.modify_position(
                            position_id=position_id,
                            stop_loss=new_sl,
                            take_profit=position.get('takeProfit')
                        )
                        send_telegram(f"🛡️ Riobot: XAUUSD v zisku {profit:.2f}$ -> SL posunutý na +1 BE!")

            # 2. Kontrola trhu a vstupná logika (1h sviečky, Fibo, EMA)
            candles = await connection.get_candles(symbol='XAUUSD', timeframe='1h', count=200)
            if len(candles) >= 200:
                closes = [c['close'] for c in candles]
                highs = [c['high'] for c in candles]
                lows = [c['low'] for c in candles]

                ema_50 = calculate_ema(closes, 50)
                ema_200 = calculate_ema(closes, 200)
                current_price = closes[-1]

                # Určenie swingu za posledných 50 hodín pre Fibo
                swing_high = max(highs[-50:])
                swing_low = min(lows[-50:])
                diff = swing_high - swing_low

                # Fibonacciho hladiny 0.5 a 0.618 pre rastúci trend
                fibo_50 = swing_high - (diff * 0.5)
                fibo_618 = swing_high - (diff * 0.618)

                # Podmienka pre BUY: Trend je rastúci (EMA 50 > EMA 200) a cena koriguje do Fibo zóny
                if ema_50 > ema_200 and (fibo_618 <= current_price <= fibo_50):
                    if len(positions) == 0:  # Otvoríme len ak nemáme inú otvorenú pozíciu
                        tp_price = current_price + 6.0
                        sl_price = swing_low - 1.0  # Pod swing low

                        await connection.create_market_buy_order(
                            symbol='XAUUSD',
                            volume=0.01,
                            stop_loss=sl_price,
                            take_profit=tp_price,
                            comment="Riobot 1h Fibo"
                        )
                        send_telegram(f"🟢 Riobot otvoril BUY XAUUSD na základe 1h Fibo korekcie! Cena: {current_price}")

        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            
        await asyncio.sleep(300)  # Kontrola každých 5 minút

if __name__ == "__main__":
    asyncio.run(bot_loop())
