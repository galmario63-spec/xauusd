import os
import time
import asyncio
import pandas as pd
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests

app = Flask('')

@app.route('/')
def home():
    return "Riobot XAUUSD Active"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = "XAUUSD"
LOT_SIZE = 0.01

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: 
        print("Chýba Telegram token alebo chat ID!")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=5)
        print(f"Telegram odpoveď: {res.status_code}")
    except Exception as e:
        print(f"Chyba odoslania Telegram: {e}")

async def run_bot():
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    await account.wait_connected()
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    send_telegram("🚀 *Riobot je pripojený, Telegram notifikácie sú aktívne!*")

    while True:
        try:
            positions = await connection.get_positions()
            
            # Správa Break-Even
            for pos in positions:
                if pos['symbol'] == SYMBOL:
                    if pos.get('profit', 0) >= 3.0 and pos.get('stopLoss', 0) == 0:
                        op = pos.get('openPrice', 0)
                        sl = op + 1.0 if pos['type'] == 'POSITION_TYPE_BUY' else op - 1.0
                        await connection.modify_position(pos['id'], stop_loss=sl, take_profit=pos.get('takeProfit', 0))
                        send_telegram(f"🛡️ *Break-Even aktivovaný* pre pozíciu {pos['id']}")

            symbol_positions = [p for p in positions if p['symbol'] == SYMBOL]

            if len(symbol_positions) == 0:
                price_info = await connection.get_symbol_price(SYMBOL)
                bid = price_info.get('bid')
                ask = price_info.get('ask')
                
                if ask and bid:
                    # Presné nastavenie TP (+6.0) a SL (-10.0)
                    tp = ask + 6.0
                    sl = ask - 10.0
                    await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)
                    
                    msg = f"🟢 *XAUUSD BUY Obchod otvorený!*\nCena: {ask}\nTP: {tp:.2f}\nSL: {sl:.2f}"
                    send_telegram(msg)
                    print(msg)

            await asyncio.sleep(20)
        except Exception as e:
            print(f"Chyba v cykle: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
