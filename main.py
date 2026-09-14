import os
import time
import asyncio
from datetime import datetime
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
    send_telegram("🚀 Riobot štartuje s ladičením chýb...")
    
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
                
            send_telegram("🚀 Riobot pripojený a sleduje chyby!")
            
            while True:
                try:
                    positions = await connection.get_positions()
                except Exception as pos_err:
                    await asyncio.sleep(3)
                    continue
                
                btc_positions = [p for p in positions if p['symbol'] == 'BTCUSD']
                gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']

                try:
                    candles_gold = await connection.get_candles('XAUUSD', timeframe='5m', limit=3)
                    candles_btc = await connection.get_candles('BTCUSD', timeframe='5m', limit=3)
                    
                    # Zlato
                    if len(gold_positions) == 0 and len(candles_gold) >= 2:
                        c_curr = candles_gold[-1]
                        symbol_price = await connection.get_symbol_price('XAUUSD')
                        if c_curr['close'] > c_curr['open']:
                            ask = symbol_price['ask']
                            await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=ask - 14, take_profit=ask + 10)
                            send_telegram(f"🟢 XAUUSD BUY!")
                        elif c_curr['close'] < c_curr['open']:
                            bid = symbol_price['bid']
                            await connection.create_market_sell_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=bid + 14, take_profit=bid - 10)
                            send_telegram(f"🔴 XAUUSD SELL!")

                    # BTC
                    if len(btc_positions) == 0 and len(candles_btc) >= 2:
                        c_curr = candles_btc[-1]
                        symbol_price = await connection.get_symbol_price('BTCUSD')
                        if c_curr['close'] > c_curr['open']:
                            ask = symbol_price['ask']
                            await connection.create_market_buy_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=ask - 100, take_profit=ask + 150)
                            send_telegram(f"🟢 BTCUSD BUY!")
                        elif c_curr['close'] < c_curr['open']:
                            bid = symbol_price['bid']
                            await connection.create_market_sell_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=bid + 100, take_profit=bid - 150)
                            send_telegram(f"🔴 BTCUSD SELL!")
                            
                except Exception as candle_err:
                    send_telegram(f"⚠️ Chyba bota: {str(candle_err)[:100]}")

                await asyncio.sleep(3)
                
        except Exception as e:
            send_telegram(f"⚠️ Výpadok spojenia: {str(e)[:100]}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
