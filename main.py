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
    send_telegram("🚀 Riobot beží s ochranou BE...")
    
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
                
            send_telegram("🚀 Riobot pripojený, stráži ceny aj BE!")
            
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
                    symbol_prices = {
                        'XAUUSD': await connection.get_symbol_price('XAUUSD'),
                        'BTCUSD': await connection.get_symbol_price('BTCUSD')
                    }
                    
                    g_bid = symbol_prices['XAUUSD']['bid']
                    g_ask = symbol_prices['XAUUSD']['ask']
                    b_bid = symbol_prices['BTCUSD']['bid']
                    b_ask = symbol_prices['BTCUSD']['ask']

                    # --- BE BTC ---
                    for p in btc_positions:
                        open_price = p['openPrice']
                        current_sl = p.get('stopLoss', 0)
                        if p['type'] == 'POSITION_TYPE_BUY' and (b_bid - open_price) >= 7 and current_sl < open_price + 1:
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price + 1, take_profit=p['takeProfit'])
                            send_telegram("🛡️ BTC BUY -> BE (+1)")
                        elif p['type'] == 'POSITION_TYPE_SELL' and (open_price - b_ask) >= 7 and (current_sl > open_price - 1 or current_sl == 0):
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1, take_profit=p['takeProfit'])
                            send_telegram("🛡️ BTC SELL -> BE (-1)")

                    # --- BE ZLATO ---
                    for p in gold_positions:
                        open_price = p['openPrice']
                        current_sl = p.get('stopLoss', 0)
                        if p['type'] == 'POSITION_TYPE_BUY' and (g_bid - open_price) >= 6 and current_sl < open_price + 1.5:
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price + 1.5, take_profit=p['takeProfit'])
                            send_telegram("🛡️ XAU BUY -> BE (+1.5)")
                        elif p['type'] == 'POSITION_TYPE_SELL' and (open_price - g_ask) >= 6 and (current_sl > open_price - 1.5 or current_sl == 0):
                            await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1.5, take_profit=p['takeProfit'])
                            send_telegram("🛡️ XAU SELL -> BE (-1.5)")

                    # --- VSTUPY ---
                    if len(gold_positions) == 0 and last_gold is not None:
                        if g_bid > last_gold:
                            await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=g_ask - 14, take_profit=g_ask + 10)
                            send_telegram("🟢 XAUUSD BUY!")
                        elif g_bid < last_gold:
                            await connection.create_market_sell_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=g_bid + 14, take_profit=g_bid - 10)
                            send_telegram("🔴 XAUUSD SELL!")
                    last_gold = g_bid

                    if len(btc_positions) == 0 and last_btc is not None:
                        if b_bid > last_btc:
                            await connection.create_market_buy_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=b_ask - 100, take_profit=b_ask + 150)
                            send_telegram("🟢 BTCUSD BUY!")
                        elif b_bid < last_btc:
                            await connection.create_market_sell_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=b_bid + 100, take_profit=b_bid - 150)
                            send_telegram("🔴 BTCUSD SELL!")
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
