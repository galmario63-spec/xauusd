import os
import time
import asyncio
import aiohttp
import http.server
import socketserver
import threading
from metaapi_cloud_sdk import MetaApi

# --- ŠTANDARDNÝ HTTP SERVER PRE RAILWAY (bez externých závislostí) ---
PORT = int(os.environ.get("PORT", 8080))

class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Riobot is active")
    def log_message(self, format, *args):
        pass # Vypisovanie logov servera je vypnuté, aby nešpammovali

def run_server():
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), HealthHandler) as httpd:
            httpd.serve_forever()
    except Exception:
        pass

# Spustenie servera na pozadí, aby Railway nezhadzovalo kontajner
threading.Thread(target=run_server, daemon=True).start()

# --- KONFIGURÁCIA BOTA ---
TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'Tvoj_Telegram_Bot_Token')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'Tvoj_Chat_ID')

FIB_MIN = 0.50
FIB_MAX = 0.618
LOT_SIZE = 0.01

price_history = []

async def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as response:
                await response.text()
    except Exception as e:
        print(f"Telegram chyba: {e}")

async def main():
    print("Riobot štartuje...")
    
    # Bezpečné pripojenie s opakovaním pri výpadku
    connection = None
    while True:
        try:
            metaapi = MetaApi(TOKEN)
            account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
            
            if account.state != 'DEPLOYED':
                await account.deploy()
                await asyncio.sleep(10)
            
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
            
            print("Riobot stabilne pripojený. SL: 12 | TP1: 6 | TP2: 9 | BE pri zisku 3 -> +1")
            await send_telegram("🤖 Riobot je online: SL 12, TP (6/9), BE +1 pri zisku 3.")
            break
        except Exception as e:
            print(f"Chyba pripojenia, skúšam znova o 10s: {e}")
            await asyncio.sleep(10)

    # Hlavná slučka bota
    while True:
        try:
            await asyncio.sleep(20)

            price_data = await connection.get_symbol_price(SYMBOL)
            if not price_data: 
                continue

            current_bid = price_data.get('bid')
            current_ask = price_data.get('ask')
            if not current_bid or not current_ask: 
                continue

            price_history.append(current_bid)
            if len(price_history) > 30:
                price_history.pop(0)

            # 1. Kontrola Break-Even (zisk 3 -> posun SL na +1)
            positions = []
            try:
                positions = await connection.get_positions()
            except Exception:
                pass

            for p in positions:
                if p.get('symbol') == SYMBOL:
                    open_price = p.get('openPrice', 0)
                    pos_type = str(p.get('type', ''))
                    current_sl = p.get('stopLoss', 0)
                    pos_id = p.get('id')
                    
                    if 'BUY' in pos_type:
                        profit = current_bid - open_price
                        target_sl = open_price + 1.0
                        if profit >= 3.0 and (current_sl == 0 or current_sl < target_sl):
                            try:
                                await connection.modify_position(pos_id, stop_loss=target_sl, take_profit=p.get('takeProfit'))
                                print(f"BE aktivovaný pre BUY! SL na {target_sl}")
                                await send_telegram(f"🛡️ Break-Even na XAUUSD BUY: SL posunutý na +1$ ({target_sl})")
                            except Exception as e:
                                print(f"Chyba pri úpravie SL pre BUY: {e}")
                                
                    elif 'SELL' in pos_type:
                        profit = open_price - current_ask
                        target_sl = open_price - 1.0
                        if profit >= 3.0 and (current_sl == 0 or current_sl > target_sl):
                            try:
                                await connection.modify_position(pos_id, stop_loss=target_sl, take_profit=p.get('takeProfit'))
                                print(f"BE aktivovaný pre SELL! SL na {target_sl}")
                                await send_telegram(f"🛡️ Break-Even na XAUUSD SELL: SL posunutý na +1$ ({target_sl})")
                            except Exception as e:
                                print(f"Chyba pri úpravie SL pre SELL: {e}")

            # 2. Vstupy na základe Fibonacciho zóny (0.5 - 0.618)
            has_position = any(p.get('symbol') == SYMBOL for p in positions)
            if not has_position and len(price_history) >= 20:
                max_price = max(price_history)
                min_price = min(price_history)
                diff = max_price - min_price

                if diff > 0:
                    fib_50 = max_price - (diff * FIB_MIN)
                    fib_618 = max_price - (diff * FIB_MAX)
                    zone_min = min(fib_50, fib_618)
                    zone_max = max(fib_50, fib_618)

                    # BUY logika
                    if min_price < current_bid and (zone_min <= current_bid <= zone_max):
                        sl = current_bid - 12.0
                        tp1 = current_bid + 6.0
                        tp2 = current_bid + 9.0
                        
                        try:
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp1)
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp2)
                            
                            msg = f"🔴 XAUUSD BUY – RIO_ENGINE\n\nEntry: {current_bid}\nTP1 (6$): {tp1}\nTP2 (9$): {tp2}\nSL: {sl}"
                            await send_telegram(msg)
                            price_history.clear()
                        except Exception as e:
                            print(f"Chyba pri otváraní BUY obchodu: {e}")

                    # SELL logika
                    elif max_price > current_bid and (zone_min <= current_bid <= zone_max):
                        sl = current_bid + 12.0
                        tp1 = current_bid - 6.0
                        tp2 = current_bid - 9.0
                        
                        try:
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp1)
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp2)
                            
                            msg = f"🔴 XAUUSD SELL – RIO_ENGINE\n\nEntry: {current_bid}\nTP1 (6$): {tp1}\nTP2 (9$): {tp2}\nSL: {sl}"
                            await send_telegram(msg)
                            price_history.clear()
                        except Exception as e:
                            print(f"Chyba pri otváraní SELL obchodu: {e}")

        except Exception as e:
            print(f"Chyba v hlavnej slučke: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
