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
    return "Riobot Master Engine - Online"

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

BTC_LOT = 0.10
GOLD_LOT = 0.01

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
    
    send_telegram("🚀 Riobot Master štartuje (Zlato + BTC)...")
    
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
                
            send_telegram("🚀 Riobot pripojený a riadi oba trhy!")
            
            while True:
                positions = await connection.get_positions()
                
                btc_positions = [p for p in positions if p['symbol'] == 'BTCUSD']
                gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']
                
                # --- SPRÁVA BTC ---
                for p in btc_positions:
                    open_price = p['openPrice']
                    p_type = p['type']
                    current_sl = p.get('stopLoss', 0)
                    symbol_price = await connection.get_symbol_price('BTCUSD')
                    bid = symbol_price['bid']
                    ask = symbol_price['ask']
                    
                    if p_type == 'POSITION_TYPE_BUY':
                        profit = bid - open_price
                        if profit >= 4 and current_sl < open_price + 1:
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price + 1, take_profit=p['takeProfit'])
                            send_telegram(f"🛡️ BTCUSD BUY posunutý do BE (+1)!")
                    elif p_type == 'POSITION_TYPE_SELL':
                        profit = open_price - ask
                        if profit >= 4 and (current_sl > open_price - 1 or current_sl == 0):
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1, take_profit=p['takeProfit'])
                            send_telegram(f"🛡️ BTCUSD SELL posunutý do BE (-1)!")

                # --- SPRÁVA ZLATO ---
                for p in gold_positions:
                    open_price = p['openPrice']
                    p_type = p['type']
                    current_sl = p.get('stopLoss', 0)
                    symbol_price = await connection.get_symbol_price('XAUUSD')
                    bid = symbol_price['bid']
                    ask = symbol_price['ask']
                    
                    if p_type == 'POSITION_TYPE_BUY':
                        profit = bid - open_price
                        if profit >= 1.5 and current_sl < open_price + 1:
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price + 1, take_profit=p['takeProfit'])
                            send_telegram(f"🛡️ XAUUSD BUY posunutý do BE (+1)!")
                    elif p_type == 'POSITION_TYPE_SELL':
                        profit = open_price - ask
                        if profit >= 1.5 and (current_sl > open_price - 1 or current_sl == 0):
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1, take_profit=p['takeProfit'])
                            send_telegram(f"🛡️ XAUUSD SELL posunutý do BE (-1)!")

                # --- VSTUPY (ak pozície nie sú) ---
                if len(btc_positions) == 0:
                    try:
                        symbol_price = await connection.get_symbol_price('BTCUSD')
                        ask = symbol_price['ask']
                        sl = ask - 200  # Bezpečnejší odstup pre BTC
                        tp = ask + 400
                        await connection.create_market_buy_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=sl, take_profit=tp)
                        send_telegram(f"🟢 BTCUSD BUY otvorený (0.10)!")
                    except Exception as btc_err:
                        print(f"BTC skip: {btc_err}")

                if len(gold_positions) == 0:
                    try:
                        symbol_price = await connection.get_symbol_price('XAUUSD')
                        ask = symbol_price['ask']
                        sl = ask - 12
                        tp = ask + 10
                        await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=sl, take_profit=tp)
                        send_telegram(f"🟢 XAUUSD BUY otvorený (0.01)!")
                    except Exception as gold_err:
                        print(f"Gold skip: {gold_err}")

                await asyncio.sleep(10)
                
        except Exception as e:
            print(f"Chyba spojenia: {e}")
            send_telegram(f"⚠️ Obnovujem spojenie s brokerom...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_bot())
