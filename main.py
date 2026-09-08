import os
import time
import json
import urllib.request
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

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

def api_get(endpoint):
    req = urllib.request.Request(f"{BASE_URL}{endpoint}", headers={"auth-token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode('utf-8'))
    except Exception:
        pass
    return None

def api_post(endpoint, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(f"{BASE_URL}{endpoint}", data=data, headers={"auth-token": TOKEN, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except Exception:
        return None

def trading_bot_loop():
    print("Riobot štartuje: Lot 0.01, SL 7$, TP 6$, BE pri 2$ -> +1$.")
    send_telegram("🤖 Riobot online: Lot 0.01, SL 7$, TP 6$, BE pri 2$ -> +1$.")
    
    price_history = []

    while True:
        try:
            time.sleep(20)

            price_data = api_get(f"/symbols/{SYMBOL}/price")
            if not price_data:
                continue

            current_bid = price_data.get('bid')
            current_ask = price_data.get('ask')
            if not current_bid or not current_ask:
                continue

            price_history.append(current_bid)
            if len(price_history) > 30:
                price_history.pop(0)

            positions = api_get("/positions") or []

            for p in positions:
                if p.get('symbol') == SYMBOL:
                    open_price = p.get('openPrice', 0)
                    pos_type = str(p.get('type', ''))
                    current_sl = p.get('stopLoss', 0)
                    pos_id = p.get('id')
                    
                    if 'BUY' in pos_type:
                        profit = current_bid - open_price
                        target_sl = open_price + 1.0
                        if profit >= 2.0 and (current_sl == 0 or current_sl < target_sl):
                            payload = {"actionType": "MODIFY_POSITION", "stopLoss": target_sl, "takeProfit": p.get('takeProfit', 0)}
                            status = api_post(f"/positions/{pos_id}", payload)
                            if status == 200:
                                send_telegram(f"🛡️ Break-Even BUY: SL na +1$ ({target_sl})")
                                        
                    elif 'SELL' in pos_type:
                        profit = open_price - current_ask
                        target_sl = open_price - 1.0
                        if profit >= 2.0 and (current_sl == 0 or current_sl > target_sl):
                            payload = {"actionType": "MODIFY_POSITION", "stopLoss": target_sl, "takeProfit": p.get('takeProfit', 0)}
                            status = api_post(f"/positions/{pos_id}", payload)
                            if status == 200:
                                send_telegram(f"🛡️ Break-Even SELL: SL na +1$ ({target_sl})")

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

                    if min_price < current_bid and (zone_min <= current_bid <= zone_max):
                        sl = current_bid - 7.0
                        tp = current_bid + 6.0
                        order_payload = {
                            "actionType": "ORDER_TYPE_BUY",
                            "symbol": SYMBOL,
                            "volume": 0.01,
                            "stopLoss": sl,
                            "takeProfit": tp
                        }
                        status = api_post("/orders", order_payload)
                        if status == 200:
                            send_telegram(f"🟢 XAUUSD BUY (0.01)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                            price_history.clear()

                    elif max_price > current_bid and (zone_min <= current_bid <= zone_max):
                        sl = current_bid + 7.0
                        tp = current_bid - 6.0
                        order_payload = {
                            "actionType": "ORDER_TYPE_SELL",
                            "symbol": SYMBOL,
                            "volume": 0.01,
                            "stopLoss": sl,
                            "takeProfit": tp
                        }
                        status = api_post("/orders", order_payload)
                        if status == 200:
                            send_telegram(f"🔴 XAUUSD SELL (0.01)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                            price_history.clear()

        except Exception as e:
            print(f"Chyba: {e}")
            time.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=trading_bot_loop, daemon=True).start()
    while True:
        time.sleep(3600)
