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
    return "Riobot XAUUSD Safe Mode"

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

last_trade_time = 0
COOLDOWN_SECONDS = 900  # 15 minút pauza medzi obchodmi

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: 
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

async def run_bot():
    global last_trade_time
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    await account.wait_connected()
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    send_telegram("Riobot aktualizovany: SL 15, TP 10, pauza 15m + BE pri +3 na +1.5!")

    while True:
        try:
            positions = await connection.get_positions()
            
            # Break-Even manažment: pri +3 zisk posunúť SL na +1.5 od vstupu
            for pos in positions:
                if pos['symbol'] == SYMBOL and pos['type'] == 'POSITION_TYPE_BUY':
                    op = pos.get('openPrice', 0)
                    current_sl = pos.get('stopLoss', 0)
                    price_info = await connection.get_symbol_price(SYMBOL)
                    bid = price_info.get('bid', 0)
                    
                    if bid and op:
                        current_profit_points = bid - op
                        # Ak je zisk >= 3 body a SL ešte nie je na +1.5
                        if current_profit_points >= 3.0 and current_sl < (op + 1.5):
                            new_sl = op + 1.5
                            await connection.modify_position(pos['id'], stop_loss=new_sl, take_profit=pos.get('takeProfit', 0))
                            send_telegram(f"Break-Even aktivovaný! SL posunutý na +1.5 (Cena: {new_sl:.2f})")

            symbol_positions = [p for p in positions if p['symbol'] == SYMBOL]
            current_time = time.time()

            if len(symbol_positions) == 0 and (current_time - last_trade_time >= COOLDOWN_SECONDS):
                price_info = await connection.get_symbol_price(SYMBOL)
                bid = price_info.get('bid')
                ask = price_info.get('ask')
                
                if ask and bid:
                    tp = ask + 10.0
                    sl = ask - 15.0
                    
                    await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)
                    last_trade_time = time.time()
                    
                    msg = f"XAUUSD Obchod otvoreny\nCena: {ask}\nTP: {tp:.2f}\nSL: {sl:.2f}"
                    send_telegram(msg)
                    print(msg)

            await asyncio.sleep(30)
        except Exception as e:
            print(f"Chyba: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
