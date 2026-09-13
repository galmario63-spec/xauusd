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
    
    # 1. Inicializácia prebehne IBA RAZ pri štarte
    send_telegram("🚀 Riobot sa inicializuje...")
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
        
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
        
    send_telegram("🚀 Riobot pripojený, aktívne obchoduje BUY aj SELL (bez Stochastiku)!")
    
    # 2. Hlavná slučka
    while True:
        try:
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]
            
            symbol_price = await connection.get_symbol_price(SYMBOL)
            current_bid = symbol_price['bid']
            current_ask = symbol_price['ask']
            
            # Správa otvorených pozícií (Break-Even kontrola)
            for p in btc_positions:
                open_price = p['openPrice']
                p_type = p['type']
                current_sl = p.get('stopLoss', 0)
                
                if p_type == 'POSITION_TYPE_BUY':
                    profit_points = current_bid - open_price
                    if profit_points >= 300 and (current_sl < open_price or current_sl == 0):
                        await connection.modify_position(
                            position_id=p['id'],
                            stop_loss=open_price,
                            take_profit=p['takeProfit']
                        )
                        send_telegram(f"🛡️ Riobot posunul BUY pozíciu do Break-Even na {open_price}!")
                        
                elif p_type == 'POSITION_TYPE_SELL':
                    profit_points = open_price - current_ask
                    if profit_points >= 300 and (current_sl > open_price or current_sl == 0):
                        await connection.modify_position(
                            position_id=p['id'],
                            stop_loss=open_price,
                            take_profit=p['takeProfit']
                        )
                        send_telegram(f"🛡️ Riobot posunul SELL pozíciu do Break-Even na {open_price}!")

            # Ak nemá otvorenú pozíciu, okamžite vstupuje do obchodu na základe aktuálneho pohybu
            if len(btc_positions) == 0:
                # Jednoduchá a rýchla podmienka pre okamžitý vstup (žiadne zbytočné čakanie)
                # Otvoríme BUY s pripraveným SL a TP
                sl_buy = current_ask - 400
                tp_buy = current_ask + 800
                
                result = await connection.create_market_buy_order(
                    symbol=SYMBOL,
                    volume=LOT_SIZE,
                    stop_loss=sl_buy,
                    take_profit=tp_buy,
                    comment="riobot-buy"
                )
                send_telegram(f"🟢 Riobot práve otvoril BUY obchod na {current_ask} (SL: {sl_buy}, TP: {tp_buy})!")

            await asyncio.sleep(15)
            
        except Exception as e:
            print(f"Chyba v cykle: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_bot())
