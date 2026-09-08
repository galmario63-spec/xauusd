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

TOKEN = os.getenv('METAAPI_TOKEN', '')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
BASE_URL = f"https://mt-client-api-v1.london.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}"

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode('utf-8')
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}), timeout=10)
    except Exception: pass

def api_get(endpoint):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{BASE_URL}{endpoint}", headers={"auth-token": TOKEN, "Content-Type": "application/json"}), timeout=10) as resp:
            if resp.status == 200: return json.loads(resp.read().decode('utf-8'))
    except Exception: pass
    return None

def api_post(endpoint, payload):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{BASE_URL}{endpoint}", data=json.dumps(payload).encode('utf-8'), headers={"auth-token": TOKEN, "Content-Type": "application/json"}, method="POST"), timeout=10) as resp:
            return resp.status
    except Exception: return None

def trading_bot_loop():
    print("Riobot štartuje: Lot 0.01, SL 7$, TP 6$, BE pri 2$")
    send_telegram("🤖 Riobot online: Lot 0.01, SL 7$, TP 6$, BE pri 2$.")
    price_history = []
    while True:
        try:
            time.sleep(20)
            price_data = api_get(f"/symbols/{SYMBOL}/price")
            if not price_data: continue
            bid, ask = price_data.get('bid'), price_data.get('ask')
            if not bid or not ask: continue
            price_history.append(bid)
            if len(price_history) > 30: price_history.pop(0)
            positions = api_get("/positions") or []
            for p in positions:
                if p.get('symbol') == SYMBOL:
                    op, pt, sl, pid = p.get('openPrice', 0), str(p.get('type', '')), p.get('stopLoss', 0), p.get('id')
                    if 'BUY' in pt and (bid - op) >= 2.0 and (sl == 0 or sl < op + 1.0):
                        if api_post(f"/positions/{pid}", {"actionType": "MODIFY_POSITION", "stopLoss": op + 1.0, "takeProfit": p.get('takeProfit', 0)}) == 200:
                            send_telegram("🛡️ Break-Even BUY: SL na +1$")
                    elif 'SELL' in pt and (op - ask) >= 2.0 and (sl == 0 or sl > op - 1.0):
                        if api_post(f"/positions/{pid}", {"actionType": "MODIFY_POSITION", "stopLoss": op - 1.0, "takeProfit": p.get('takeProfit', 0)}) == 200:
                            send_telegram("🛡️ Break-Even SELL: SL na +1$")
            if not any(p.get('symbol') == SYMBOL for p in positions) and len(price_history) >= 20:
                mx, mn = max(price_history), min(price_history)
                diff = mx - mn
                if diff > 0:
                    z1, z2 = mx - (diff * 0.50), mx - (diff * 0.618)
                    zm, zmx = min(z1, z2), max(z1, z2)
                    if mn < bid and zm <= bid <= zmx:
                        if api_post("/orders", {"actionType": "ORDER_TYPE_BUY", "symbol": SYMBOL, "volume": 0.01, "stopLoss": bid - 7.0, "takeProfit": bid + 6.0}) == 200:
                            send_telegram(f"🟢 BUY (0.01)\nEntry: {bid}")
                            price_history.clear()
                    elif mx > bid and zm <= bid <= zmx:
                        if api_post("/orders", {"actionType": "ORDER_TYPE_SELL", "symbol": SYMBOL, "volume": 0.01, "stopLoss": bid + 7.0, "takeProfit": bid - 6.0}) == 200:
                            send_telegram(f"🔴 SELL (0.01)\nEntry: {bid}")
                            price_history.clear()
        except Exception: time.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=trading_bot_loop, daemon=True).start()
    while True: time.sleep(3600)
