import os
import time
import asyncio
import aiohttp
import http.server
import socketserver
import threading

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

BASE_URL = f"https://mt-client-api-v1.london.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}"

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
    print("Riobot štartuje cez REST API (bez SDK chýb)...")
    await send_telegram("🤖 Riobot online: Prešiel na stabilné REST API.")

    headers = {
        "auth-token": TOKEN,
        "Content-Type": "application/json"
    }

    price_history = []

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                await asyncio.sleep(20)

                # 1. Získanie ceny cez REST API
                async with session.get(f"{BASE_URL}/symbols/{SYMBOL}/price", timeout=10) as resp:
                    if resp.status != 200:
                        continue
                    price_data = await resp.json()

                current_bid = price_data.get('bid')
                current_ask = price_data.get('ask')
                if not current_bid or not current_ask:
                    continue

                price_history.append(current_bid)
                if len(price_history) > 30:
                    price_history.pop(0)

                # 2. Kontrola pozícií a Break-Even cez REST API
                async with session.get(f"{BASE_URL}/positions", timeout=10) as resp:
                    positions = await resp.json() if resp.status == 200 else []

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
                                payload = {"actionType": "MODIFY_POSITION", "stopLoss": target_sl, "takeProfit": p.get('takeProfit', 0)}
                                async with session.post(f"{BASE_URL}/positions/{pos_id}", json=payload) as mod_resp:
                                    if mod_resp.status == 200:
                                        print(f"BE aktívny pre BUY na {target_sl}")
                                        await send_telegram(f"🛡️ Break-Even BUY: SL na +1$ ({target_sl})")
                                        
                        elif 'SELL' in pos_type:
                            profit = open_price - current_ask
                            target_sl = open_price - 1.0
                            if profit >= 3.0 and (current_sl == 0 or current_sl > target_sl):
                                payload = {"actionType": "MODIFY_POSITION", "stopLoss": target_sl, "takeProfit": p.get('takeProfit', 0)}
                                async with session.post(f"{BASE_URL}/positions/{pos_id}", json=payload) as mod_resp:
                                    if mod_resp.status == 200:
                                        print(f"BE aktívny pre SELL na {target_sl}")
                                        await send_telegram(f"🛡️ Break-Even SELL: SL na +1$ ({target_sl})")

                # 3. Vstupy (Fibonacci zóna)
                has_position = any(p.get('symbol') == SYMBOL for p in positions)
                if not has_position and len(price_history) >= 20:
                    max_price = max(price_history)
                    min_price = min(price_history)
                    diff = max_price - min_price

                    if diff > 0:
                        fib_50 = max_price - (diff * 0.50)
                        fib_618 = max_price - (diff * 0.618)
                        zone_min = min(fib_50, fib_618)
                        zone_max = max(fib_50, fib_618)

                        # BUY
                        if min_price < current_bid and (zone_min <= current_bid <= zone_max):
                            sl = current_bid - 12.0
                            tp = current_bid + 9.0
                            order_payload = {
                                "actionType": "ORDER_TYPE_BUY",
                                "symbol": SYMBOL,
                                "volume": 0.02,
                                "stopLoss": sl,
                                "takeProfit": tp
                            }
                            async with session.post(f"{BASE_URL}/orders", json=order_payload) as ord_resp:
                                if ord_resp.status == 200:
                                    await send_telegram(f"🟢 XAUUSD BUY (0.02)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                                    price_history.clear()

                        # SELL
                        elif max_price > current_bid and (zone_min <= current_bid <= zone_max):
                            sl = current_bid + 12.0
                            tp = current_bid - 9.0
                            order_payload = {
                                "actionType": "ORDER_TYPE_SELL",
                                "symbol": SYMBOL,
                                "volume": 0.02,
                                "stopLoss": sl,
                                "takeProfit": tp
                            }
                            async with session.post(f"{BASE_URL}/orders", json=order_payload) as ord_resp:
                                if ord_resp.status == 200:
                                    await send_telegram(f"🔴 XAUUSD SELL (0.02)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                                    price_history.clear()

            except Exception as e:
                print(f"Chyba v REST cykle: {e}")
                await asyncio.sleep(10)

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            print(f"Global error: {e}")
            time.sleep(5)
