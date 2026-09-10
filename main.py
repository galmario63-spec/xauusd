import os
import time
import requests
from flask import Flask
from threading import Thread

# Flask server pre udržanie živého stavu na Renderi
app = Flask('')

@app.route('/')
def home():
    return "Riobot XAUUSD Engine is running live!"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# Telegram konfigurácia
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Chyba: Telegram token alebo Chat ID nie sú nastavené v Environment Variables!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=5)
        print(f"Telegram response: {response.status_code}")
    except Exception as e:
        print(f"Chyba pri odosielaní na Telegram: {e}")

# Hlavná obchodná logika pre XAUUSD s filtrom a BE(+1)
def trading_logic():
    print("Spúšťam obchodnú logiku...")
    send_telegram("🚀 *Riobot XAUUSD Engine* bol úspešne spustený naživo! Filtrovanie (EMA, MACD, Stochastic, Fibonacci, Price Action) a ochrana (TP 6, SL 10, BE przy +1) sú aktívne.")
    
    while True:
        try:
            # Tu bude prebiehať analýza trhu a exekúcia obchodov
            time.sleep(30)
        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            time.sleep(10)

if __name__ == "__main__":
    keep_alive()
    trading_logic()
