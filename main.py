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
    return "Riobot XAU Active"

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

GOLD_LOT = 0.05
POINT = 0.01  # 1 bod na zlato = 0.01 USD

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

async def run_bot():
    send_telegram("🚀 Riobot nabehol: LEN ZLATO (Lot 0.05, TP 20, SL 18, BE +7 -> +2)...")
    
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
                
            send_telegram("🚀 Riobot pripojený a pripravený na zlato!")
            
            last_gold = None
            
            while True:
                try:
                    positions = await connection.get_positions()
                except Exception:
                    await asyncio.sleep(3)
                    continue
                
                gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']

                try:
                    symbol_price = await connection.get_symbol_price('XAUUSD')
                    g_bid = symbol_price['bid']
                    g_ask = symbol_price['ask']

                    # --- BE ZLATO (pri zisku +7 bodov posun SL na +2 body do zisku) ---
                    for p in gold_positions:
                        open_price = p['openPrice']
                        current_sl = p.get('stopLoss', 0)
                        
                        if p['type'] == 'POSITION_TYPE_BUY':
                            profit_points = (g_bid - open_price) / POINT
                            target_sl = open_price + (2 * POINT)
                            if profit_points >= 7 and (current_sl < target_sl):
                                await connection.modify_position(position_id=p['id'], stop_loss=target_sl, take_profit=p['takeProfit'])
                                send_telegram("🛡️ XAU BUY -> BE (+2 v zisku)")
                                
                        elif p['type'] == 'POSITION_TYPE_SELL':
                            profit_points = (open_price - g_ask) / POINT
                            target_sl = open_price - (2 * POINT)
                            if profit_points >= 7 and (current_sl > target_sl or current_sl == 0):
                                await connection.modify_position(position_id=p['id'], stop_loss=target_sl, take_profit=p['takeProfit'])
                                send_telegram("🛡️ XAU SELL -> BE (+2 v zisku)")

                    # --- VSTUPY NA ZLATO ---
                    if len(gold_positions) == 0 and last_gold is not None:
                        if g_bid > last_gold:
                            sl_val = g_ask - (18 * POINT)
                            tp_val = g_ask + (20 * POINT)
                            await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=sl_val, take_profit=tp_val)
                            send_telegram("🟢 XAUUSD BUY (0.05)!")
                            await asyncio.sleep(5)  # Pauza po obchode, aby to netrekovalo hneď ďalší
                        elif g_bid < last_gold:
                            sl_val = g_bid + (18 * POINT)
                            tp_val = g_bid - (20 * POINT)
                            await connection.create_market_sell_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=sl_val, take_profit=tp_val)
                            send_telegram("🔴 XAUUSD SELL (0.05)!")
                            await asyncio.sleep(5)
                            
                    last_gold = g_bid
                            
                except Exception as err:
                    send_telegram(f"⚠️ Chyba: {str(err)[:80]}")

                await asyncio.sleep(3)
                
        except Exception as e:
            send_telegram(f"⚠️ Výpadok: {str(e)[:80]}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
