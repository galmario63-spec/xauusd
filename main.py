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

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

async def run_bot():
    keep_alive()
    
    # 1. Inicializácia prebehne IBA RAZ pri štarte aplikácie
    send_telegram("🚀 Riobot sa inicializuje...")
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
        
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
        
    send_telegram("🚀 Riobot úspešne pripojený a trvalo stabilizovaný (bez Stochastiku)!")
    
    # 2. Hlavná nekonečná slučka len preberá dáta, nič sa už reštartuje ani nevolá znova
    while True:
        try:
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]
            
            # Sem patrí tvoja obchodná logika pre analýzu a vstup
            
            await asyncio.sleep(15)
            
        except Exception as e:
            print(f"Chyba v cykle: {e}")
            # Pri bežnej chybe pripojenia iba chvíľu počkáme bez reštartu celého API
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_bot())
