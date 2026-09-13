import os
import time
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests
import pandas as pd
import numpy as np

app = Flask('')

@app.route('/')
def home():
    return "Riobot Advanced Engine - Online"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

TELEGRAM_TOKEN = os.getenv("T_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("T_CHAT")
METAAPI_TOKEN = os.getenv("M_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("M_ACC")

SYMBOL = "BTCUSD"
LOT_SIZE = 0.1
TIMEFRAME = "5m"

last_trade_time = 0
COOLDOWN_SECONDS = 30  # Znížené z 120 na 30 sekúnd pre rýchlejší reštart

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

def calculate_indicators(candles):
    df = pd.DataFrame(candles)
    close = df['close']
    high = df['high']
    low = df['low']
    
    df['ema50'] = close.ewm(span=50, adjust=False).mean()
    df['ema200'] = close.ewm(span=200, adjust=False).mean()
    
    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    
    low_14 = low.rolling(window=14).min()
    high_14 = high.rolling(window=14).max()
    df['stoch_k'] = 100 * ((close - low_14) / (high_14 - low_14))
    df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()
    
    return df.iloc[-1]

async def run_bot():
    global last_trade_time
    keep_alive() # Spustenie Flask servera
    
    while True:
        try:
            api = MetaApi(METAAPI_TOKEN)
            account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
            
            if account.state != 'DEPLOYED':
                await account.deploy()
                
            await account.wait_connected()
            connection = account.get_rpc_connection()
            
            try:
                await connection.connect()
                await connection.wait_synchronized()
            except Exception as conn_err:
                print(f"Chyba pri synchronizácii pripojenia: {conn_err}")
                await asyncio.sleep(10)
                continue
                
            send_telegram("🚀 Riobot beží v aktualizovanom režime!")
            
            while True:
                positions = await connection.get_positions()
                current_time = time.time()
                
                btc_positions = [p for p in positions if p['symbol'] == SYMBOL]
                btc_positions_count = len(btc_positions)
                
                # Tu pokračuje tvoja logika pre získanie sviečok, volanie calculate_indicators
                # a vyhodnotenie podmienok pre vstup. 
                # Ak chceš uvoľniť filtre, skontroluj, či podmienky vyžadujú AND (všetko platí) 
                # alebo ich môžeš zmeniť na OR, prípadne vynechať napr. Stochastik.
                
                await asyncio.sleep(15) # Kontrola každých 15 sekúnd
                
        except Exception as e:
            print(f"Hlavná chyba v cykle bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_bot())
