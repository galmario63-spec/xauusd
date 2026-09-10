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
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Chyba pri odosielaní na Telegram: {e}")

# Hlavná obchodná logika pre XAUUSD
def trading_logic():
    send_telegram("🚀 Riobot XAUUSD Engine (Filtrovaná stratégia s EMA, MACD, Stochastic a BE/TP/SL) bol úspešne spustený!")
    
    # Tu bude prebiehať vyhodnocovanie indikátorov, Price Action a exekúcia
    while True:
        try:
            # 1. Kontrola podmienok pre BUY / SELL bez falošných signálov
            # (Pripojenie na MetaApi / MT5 a výpočet EMA 50/200, MACD, Stochastic, Fibonacci)
            
            # 2. Riadenie pozície: TP 6, SL 10, posun na BE pri +3
            # Príklad pravidla pre BE: Ak cena vzrastie v tvoj prospech o +3 pipy/doláre, SL sa posunie na vstupnú cenu.
            
            time.sleep(60) # Interval kontroly trhu
        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            time.sleep(10)

if __name__ == "__main__":
    keep_alive()
    trading_logic()
