import os
import time
import requests
import threading
from flask import Flask

app = Flask(__name__)

@app.route("/")
def health_check():
    return "Riobot is active", 200

TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'Tvoj_Telegram_Bot_Token')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'Tvoj_Chat_ID')

BASE_URL = f"https://mt-client-api-v1.london.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}"
HEADERS = {
    "auth-token": TOKEN,
    "Content-Type": "application/json"
}

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception:
        pass

def trading_bot_loop():
    print("Riobot obchodná slučka spustená...")
    send_telegram("🤖 Riobot online: Lot 0.02, SL 12, TP 9.")
    
    price_history = []

    while True:
        try:
            time.sleep(20)

            resp = requests.get(f"{BASE_URL}/symbols/{SYMBOL}/price", headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            price_data = resp.json()

            current_bid = price_data.get('bid')
            current_ask = price_data.get('ask')
            if not current_bid or not current_ask:
                continue

            price_history.append(current_bid)
            if len(price_history) > 30:
                price_history.pop(0)

            pos_resp = requests.get(f"{BASE_URL}/positions", headers=HEADERS, timeout=10)
            positions = pos_resp.json() if pos_resp.status_code == 200 else []

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
                            mod_resp = requests.post(f"{BASE_URL}/positions/{pos_id}", headers=HEADERS, json=payload, timeout=10)
                            if mod_resp.status_code == 200:
                                send_telegram(f"🛡️ Break-Even BUY: SL na +1$ ({target_sl})")
                                        
                    elif 'SELL' in pos_type:
                        profit = open_price - current_ask
                        target_sl = open_price - 1.0
                        if profit >= 3.0 and (current_sl == 0 or current_sl > target_sl):
                            payload = {"actionType": "MODIFY_POSITION", "stopLoss": target_sl, "takeProfit": p.get('takeProfit', 0)}
                            mod_resp = requests.post(f"{BASE_URL}/positions/{pos_id}", headers=HEADERS, json=payload, timeout=10)
                            if mod_resp.status_code == 200:
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
                        sl = current_bid - 12.0
                        tp = current_bid + 9.0
                        order_payload = {
                            "actionType": "ORDER_TYPE_BUY",
                            "symbol": SYMBOL,
                            "volume": 0.02,
                            "stopLoss": sl,
                            "takeProfit": tp
                        }
                        ord_resp = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=order_payload, timeout=10)
                        if ord_resp.status_code == 200:
                            send_telegram(f"🟢 XAUUSD BUY (0.02)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                            price_history.clear()

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
                        ord_resp = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=order_payload, timeout=10)
                        if ord_resp.status_code == 200:
                            send_telegram(f"🔴 XAUUSD SELL (0.02)\nEntry: {current_bid}\nTP: {tp}\nSL: {sl}")
                            price_history.clear()

        except Exception as e:
            print(f"Chyba: {e}")
            time.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=trading_bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
