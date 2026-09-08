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

print("Riobot štartuje: 1h Fibo + EMA stratégia, Lot 0.01, TP 6$, BE pri 2$")
send_telegram("🚀 Riobot so 1h Fibo stratégiou bol úspešne spustený na Railway!")

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

            # Opravené sťahovanie sviečok cez historické dáta MetaApi
            terminal_state = account.get_universal_terminal_state()
            await terminal_state.wait_synchronized()
            
            # Získame 1h sviečky pre XAUUSD
            candles = await connection.get_historical_candles(symbol='XAUUSD', timeframe='1h', limit=200)
            
            if len(candles) >= 200:
                closes = [c['close'] for c in candles]
                highs = [c['high'] for c in candles]
                lows = [c['low'] for c in candles]

                ema_50 = calculate_ema(closes, 50)
                ema_200 = calculate_ema(closes, 200)
                current_price = closes[-1]

                swing_high = max(highs[-50:])
                swing_low = min(lows[-50:])
                diff = swing_high - swing_low

                fibo_50 = swing_high - (diff * 0.5)
                fibo_618 = swing_high - (diff * 0.618)

                if ema_50 > ema_200 and (fibo_618 <= current_price <= fibo_50):
                    if len(positions) == 0:
                        tp_price = current_price + 6.0
                        sl_price = swing_low - 1.0

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
            
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(bot_loop())
