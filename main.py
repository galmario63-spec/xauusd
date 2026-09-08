import os, time, json, urllib.request, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# 1. Okamžitý štart servera, aby Railway nezrušilo kontajner
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

def run_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# 2. Bot
TOKEN = os.getenv('METAAPI_TOKEN', '')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
BASE_URL = f"https://mt-client-api-v1.london.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}"

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode('utf-8')
    try: urllib.request.urlopen(urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}), timeout=10)
    except: pass

def api_get(ep):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{BASE_URL}{ep}", headers={"auth-token": TOKEN, "Content-Type": "application/json"}), timeout=10) as r:
            if r.status == 200: return json.loads(r.read().decode('utf-8'))
    except: pass
    return None

def api_post(ep, payload):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{BASE_URL}{ep}", data=json.dumps(payload).encode('utf-8'), headers={"auth-token": TOKEN, "Content-Type": "application/json"}, method="POST"), timeout=10) as r:
            return r.status
    except: return None

if __name__ == "__main__":
    print("Riobot štartuje...")
    send_telegram("🤖 Riobot online.")
    history = []
    while True:
        try:
            time.sleep(20)
            p_data = api_get(f"/symbols/{SYMBOL}/price")
            if not p_data: continue
            bid, ask = p_data.get('bid'), p_data.get('ask')
            if not bid or not ask: continue
            history.append(bid)
            if len(history) > 30: history.pop(0)
            
            positions = api_get("/positions") or []
            for p in positions:
                if p.get('symbol') == SYMBOL:
                    op, pt, sl, pid = p.get('openPrice', 0), str(p.get('type', '')), p.get('stopLoss', 0), p.get('id')
                    if 'BUY' in pt and (bid - op) >= 2.0 and (sl == 0 or sl < op + 1.0):
                        if api_post(f"/positions/{pid}", {"actionType": "MODIFY_POSITION", "stopLoss": op + 1.0, "takeProfit": p.get('takeProfit', 0)}) == 200:
                            send_telegram("🛡️ BE BUY")
                    elif 'SELL' in pt and (op - ask) >= 2.0 and (sl == 0 or sl > op - 1.0):
                        if api_post(f"/positions/{pid}", {"actionType": "MODIFY_POSITION", "stopLoss": op - 1.0, "takeProfit": p.get('takeProfit', 0)}) == 200:
                            send_telegram("🛡️ BE SELL")
                            
            if not any(p.get('symbol') == SYMBOL for p in positions) and len(history) >= 20:
                mx, mn = max(history), min(history)
                diff = mx - mn
                if diff > 0:
                    z1, z2 = mx - (diff * 0.50), mx - (diff * 0.618)
                    zm, zmx = min(z1, z2), max(z1, z2)
                    if mn < bid and zm <= bid <= zmx:
                        if api_post("/orders", {"actionType": "ORDER_TYPE_BUY", "symbol": SYMBOL, "volume": 0.01, "stopLoss": bid - 7.0, "takeProfit": bid + 6.0}) == 200:
                            send_telegram(f"🟢 BUY: {bid}")
                            history.clear()
                    elif mx > bid and zm <= bid <= zmx:
                        if api_post("/orders", {"actionType": "ORDER_TYPE_SELL", "symbol": SYMBOL, "volume": 0.01, "stopLoss": bid + 7.0, "takeProfit": bid - 6.0}) == 200:
                            send_telegram(f"🔴 SELL: {bid}")
                            history.clear()
        except: time.sleep(15)
