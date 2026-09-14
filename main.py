import os
import time
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests

app = Flask('')

@app.route('/')
def home():
    return "Riobot Stopped"

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

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

async def run_bot():
    send_telegram("🛑 Riobot je ÚPLNE ZASTAVENÝ. Žiadne obchody sa neotvárajú.")
    
    while True:
        try:
            api = MetaApi(METAAPI_TOKEN)
            account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
            
            if account.state != 'DEPLOYED':
                await account.deploy()
                
            await account.wait_connected()
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
                
            # Zistíme aktuálny stav účtu
            account_info = await connection.get_account_information()
            balance = account_info.get('balance', 0)
            equity = account_info.get('equity', 0)
            
            send_telegram(f"🛡️ Stav účtu – Balance: {balance} €, Equity: {equity} €. Bot nič nerobí.")
            
            while True:
                await asyncio.sleep(300) # Len spí a nič nevykonáva
                
        except Exception as e:
            send_telegram(f"⚠️ Výpadok pripojenia: {str(e)[:80]}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
