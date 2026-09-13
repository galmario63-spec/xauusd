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
    return "Riobot BTCUSD Safe Engine"

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
LOT_SIZE = 0.01

last_trade_time = 0
COOLDOWN_SECONDS = 300  # 5 minút pauza

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
    while True:
        try:
            api = MetaApi(METAAPI_TOKEN)
            account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
            
            if account.state != 'DEPLOYED':
                await account.deploy()
            
            await account.wait_connected()
            connection = account.get_rpc_connection()
            
            # Opravené: priamo zavoláme connect bez neexistujúceho atribútu
            await connection.connect()
            await connection.wait_synchronized()
            
            send_telegram("🚀 Riobot úspešne naštartovaný a pripojený k BTCUSD!")

            while True:
                positions = await connection.get_positions()
                current_time = time.time()

                btc_positions_count = sum(1 for p in positions if p['symbol'] == SYMBOL)

                for pos in positions:
                    if pos['symbol'] == SYMBOL and pos['type'] == 'POSITION_TYPE_BUY':
                        open_price = pos['openPrice']
                        current_sl = pos.get('stopLoss', 0)
                        
                        price_info = await connection.get_symbol_price(SYMBOL)
                        bid = price_info.get('bid')

                        if bid and (bid - open_price) >= 100.0:
                            target_sl = open_price + 10.0
                            if current_sl < target_sl:
                                await connection.modify_position(
                                    positionId=pos['id'],
                                    stop_loss=target_sl,
                                    take_profit=pos.get('takeProfit', open_price + 300.0)
                                )
                                send_telegram("🔒 BE aktívne: SL posunutý do plusu!")

                if btc_positions_count == 0 and (current_time - last_trade_time) > COOLDOWN_SECONDS:
                    price_info = await connection.get_symbol_price(SYMBOL)
                    ask = price_info.get('ask')

                    if ask:
                        sl = ask - 150.0
                        tp = ask + 300.0
                        
                        await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                        last_trade_time = current_time
                        send_telegram(f"🚀 Riobot otvoril BTC obchod (SL -150, TP +300)!")

                await asyncio.sleep(5)

        except Exception as e:
            print(f"Chyba pripojenia/behu: {e}")
            send_telegram(f"⚠️ Riobot hlási chybu/výpadok pripojenia: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
