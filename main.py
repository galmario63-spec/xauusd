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
    return "Riobot Active"

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
GOLD_LOT = 0.02

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

async def run_bot():
    send_telegram("🚀 Riobot štartuje na čistú cenu...")
    
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
                
            send_telegram("🚀 Riobot pripojený a páli na základe cien!")
            
            last_btc = None
            last_gold = None
            
            while True:
                try:
                    positions = await connection.get_positions()
                except Exception:
                    await asyncio.sleep(3)
                    continue
                
                btc_positions = [p for p in positions if p['symbol'] == 'BTCUSD']
                gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']

                try:
                    # Zlato cena
                    gold_price = await connection.get_symbol_price('XAUUSD')
                    g_bid = gold_price['bid']
                    
                    if len(gold_positions) == 0 and last_gold is not None:
                        if g_bid > last_gold:
                            ask = gold_price['ask']
                            await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=ask - 14, take_profit=ask + 10)
                            send_telegram(f"🟢 XAUUSD BUY!")
                        elif g_bid < last_gold:
                            bid = gold_price['bid']
                            await connection.create_market_sell_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=bid + 14, take_profit=bid - 10)
                            send_telegram(f"🔴 XAUUSD SELL!")
                    last_gold = g_bid

                    # BTC cena
                    btc_price = await connection.get_symbol_price('BTCUSD')
                    b_bid = btc_price['bid']
                    
                    if len(btc_positions) == 0 and last_btc is not None:
                        if b_bid > last_btc:
                            ask = btc_price['ask']
                            await connection.create_market_buy_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=ask - 100, take_profit=ask + 150)
                            send_telegram(f"🟢 BTCUSD BUY!")
                        elif b_bid < last_btc:
                            bid = btc_price['bid']
                            await connection.create_market_sell_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=bid + 100, take_profit=bid - 150)
                            send_telegram(f"🔴 BTCUSD SELL!")
                    last_btc = b_bid
                            
                except Exception as err:
                    send_telegram(f"⚠️ Chyba: {str(err)[:80]}")

                await asyncio.sleep(3)
                
        except Exception as e:
            send_telegram(f"⚠️ Výpadok: {str(e)[:80]}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
