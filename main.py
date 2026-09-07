import os
import time
import asyncio
import aiohttp
import http.server
import socketserver
import threading
from metaapi_cloud_sdk import MetaApi

PORT = int(os.environ.get("PORT", 8080))

class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Riobot is active")
    def log_message(self, format, *args):
        pass

def run_server():
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), HealthHandler) as httpd:
            httpd.serve_forever()
    except Exception:
        pass

threading.Thread(target=run_server, daemon=True).start()

TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'Tvoj_Telegram_Bot_Token')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'Tvoj_Chat_ID')

FIB_MIN = 0.50
FIB_MAX = 0.618
LOT_SIZE = 0.02

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

def calculate_ema(data, period):
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

async def main():
    print("Riobot štartuje bezpečný režim...")
    
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
            
            print("Riobot stabilne pripojený. Lot: 0.02 | SL: 12 | TP: 9 | BE pri 3 -> +1")
            await send_telegram("🤖 Riobot online: Stabilný režim bez chýb SDK.")
            break
        except Exception as e:
            print(f"Chyba pripojenia, skúšam znova o 10s: {e}")
            await asyncio.sleep(10)

    while True:
        try:
            await asyncio.sleep(20)

            # Bezpečné získanie ceny cez RPC
            try:
                price_data = await connection.get_symbol_price(SYMBOL)
            except Exception:
                continue

            if not price_data: 
                continue

            current_bid = price_data.get('bid')
            current_ask = price_data.get('ask')
            if not current_bid or not current_ask: 
                continue

            price_history.append(current_bid)
            if len(price_history) > 200:
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
                                await send_telegram(f"🛡️ Break-Even BUY: SL na +1$ ({target_sl})")
                            except Exception as e:
                                print(f"Chyba úpravy SL BUY: {e}")
                                
                    elif 'SELL' in pos_type:
                        profit = open_price - current_ask
                        target_sl = open_price - 1.0
                        if profit >= 3.0 and (current_sl == 0 or current_sl > target_sl):
                            try:
                                await connection.modify_position(pos_id, stop_loss=target_sl, take_profit=p.get('takeProfit'))
                                print(f"BE aktivovaný pre SELL! SL na {target_sl}")
                                await send_telegram(f"🛡️ Break-Even SELL: SL na +1$ ({target_sl})")
                            except Exception as e:
                                print(f"Chyba úpravy SL SELL: {e}")

            # 2. Vstupy s filtrom trendu (EMA 50 a EMA 200)
            has_position = any(p.get('symbol') == SYMBOL for p in positions)
            if not has_position and len(price_history) >= 50:
                max_price = max(price_history[-30:])
                min_price = min(price_history[-30:])
                diff = max_price - min_price

                ema_50 = calculate_ema(price_history, 50)
                ema_200 = calculate_ema(price_history, min(len(price_history), 200))

                if diff > 0:
                    fib_50 = max_price - (diff * FIB_MIN)
                    fib_618 = max_price - (diff * FIB_MAX)
                    zone_min = min(fib_50, fib_618)
                    zone_max = max(fib_50, fib_618)

                    # BUY filter
                    if current_bid >= ema_200 and ema_50 > ema_200 and (zone_min <= current_bid <= zone_max):
                        sl = current_bid - 12.0
                        tp = current_bid + 9.0
                        try:
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)
                            await send_telegram(f"🟢 XAUUSD BUY (Lot 0.02)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                            price_history.clear()
                        except Exception as e:
                            print(f"Chyba BUY: {e}")

                    # SELL filter
                    elif current_bid <= ema_200 and ema_50 < ema_200 and (zone_min <= current_bid <= zone_max):
                        sl = current_bid + 12.0
                        tp = current_bid - 9.0
                        try:
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp)
                            await send_telegram(f"🔴 XAUUSD SELL (Lot 0.02)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                            price_history.clear()
                        except Exception as e:
                            print(f"Chyba SELL: {e}")

        except Exception as e:
            print(f"Chyba v slučke: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            print(f"Global catch: {e}")
            time.sleep(5)
