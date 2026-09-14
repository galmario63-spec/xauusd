import os
import time
import asyncio
from datetime import datetime, timezone
import pytz
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests

app = Flask('')

@app.route('/')
def home():
    return "Riobot Fast-Trigger Engine - Online"

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

def is_safe_to_trade():
    local_tz = pytz.timezone('Europe/Bratislava')
    now = datetime.now(local_tz)
    
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    current_time_minutes = hour * 60 + minute

    if weekday >= 5:
        return False
    if weekday == 4 and hour >= 18:
        return False
    if weekday == 0 and current_time_minutes < 15:
        return False
    if 840 <= current_time_minutes <= 1020:
        return False

    return True

async def run_bot():
    keep_alive()
    send_telegram("🚀 Riobot štartuje (Bleskový Trigger M5)...")
    
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
                
            send_telegram("🚀 Riobot pripojený! Stráži trhy každé 3 sekundy.")
            
            while True:
                try:
                    positions = await connection.get_positions()
                except Exception as pos_err:
                    print(f"Chyba pozícií: {pos_err}")
                    await asyncio.sleep(3)
                    continue
                
                btc_positions = [p for p in positions if p['symbol'] == 'BTCUSD']
                gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']
                
                # --- BE BTC ---
                for p in btc_positions:
                    try:
                        open_price = p['openPrice']
                        p_type = p['type']
                        current_sl = p.get('stopLoss', 0)
                        symbol_price = await connection.get_symbol_price('BTCUSD')
                        bid = symbol_price['bid']
                        ask = symbol_price['ask']
                        
                        if p_type == 'POSITION_TYPE_BUY':
                            profit = bid - open_price
                            if profit >= 7 and current_sl < open_price + 1:
                                await connection.modify_position(position_id=p['id'], stop_loss=open_price + 1, take_profit=p['takeProfit'])
                                send_telegram(f"🛡️ BTCUSD BUY -> BE (+1)!")
                        elif p_type == 'POSITION_TYPE_SELL':
                            profit = open_price - ask
                            if profit >= 7 and (current_sl > open_price - 1 or current_sl == 0):
                                await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1, take_profit=p['takeProfit'])
                                send_telegram(f"🛡️ BTCUSD SELL -> BE (-1)!")
                    except Exception as e:
                        print(f"BTC BE error: {e}")

                # --- BE ZLATO ---
                for p in gold_positions:
                    try:
                        open_price = p['openPrice']
                        p_type = p['type']
                        current_sl = p.get('stopLoss', 0)
                        symbol_price = await connection.get_symbol_price('XAUUSD')
                        bid = symbol_price['bid']
                        ask = symbol_price['ask']
                        
                        if p_type == 'POSITION_TYPE_BUY':
                            profit = bid - open_price
                            if profit >= 6 and current_sl < open_price + 1.5:
                                await connection.modify_position(position_id=p['id'], stop_loss=open_price + 1.5, take_profit=p['takeProfit'])
                                send_telegram(f"🛡️ XAUUSD BUY -> BE (+1.5)!")
                        elif p_type == 'POSITION_TYPE_SELL':
                            profit = open_price - ask
                            if profit >= 6 and (current_sl > open_price - 1.5 or current_sl == 0):
                                await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1.5, take_profit=p['takeProfit'])
                                send_telegram(f"🛡️ XAUUSD SELL -> BE (-1.5)!")
                    except Exception as e:
                        print(f"Gold BE error: {e}")

                # --- VSTUPY (RÝCHLY MOMENTUM TRIGGER) ---
                if is_safe_to_trade():
                    try:
                        candles_gold = await connection.get_candles('XAUUSD', timeframe='5m', limit=3)
                        candles_btc = await connection.get_candles('BTCUSD', timeframe='5m', limit=3)
                        
                        # --- ZLATO ---
                        if len(gold_positions) == 0 and len(candles_gold) >= 2:
                            c_prev = candles_gold[-2]
                            c_curr = candles_gold[-1]
                            
                            # Ak je aktuálna sviečka zelená a rastie
                            if c_curr['close'] > c_curr['open']:
                                symbol_price = await connection.get_symbol_price('XAUUSD')
                                ask = symbol_price['ask']
                                await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=ask - 14, take_profit=ask + 10)
                                send_telegram(f"🟢 XAUUSD BUY (Bleskový vstup)!")
                            # Ak je aktuálna sviečka červená a klesá
                            elif c_curr['close'] < c_curr['open']:
                                symbol_price = await connection.get_symbol_price('XAUUSD')
                                bid = symbol_price['bid']
                                await connection.create_market_sell_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=bid + 14, take_profit=bid - 10)
                                send_telegram(f"🔴 XAUUSD SELL (Bleskový vstup)!")

                        # --- BTC ---
                        if len(btc_positions) == 0 and len(candles_btc) >= 2:
                            c_prev = candles_btc[-2]
                            c_curr = candles_btc[-1]
                            
                            if c_curr['close'] > c_curr['open']:
                                symbol_price = await connection.get_symbol_price('BTCUSD')
                                ask = symbol_price['ask']
                                await connection.create_market_buy_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=ask - 100, take_profit=ask + 150)
                                send_telegram(f"🟢 BTCUSD BUY (Bleskový vstup)!")
                            elif c_curr['close'] < c_curr['open']:
                                symbol_price = await connection.get_symbol_price('BTCUSD')
                                bid = symbol_price['bid']
                                await connection.create_market_sell_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=bid + 100, take_profit=bid - 150)
                                send_telegram(f"🔴 BTCUSD SELL (Bleskový vstup)!")
                                
                    except Exception as candle_err:
                        print(f"Chyba sviečok: {candle_err}")

                await asyncio.sleep(3) # Kontrola každé 3 sekundy
                
        except Exception as e:
            print(f"Chyba spojenia: {e}")
            send_telegram(f"⚠️ Obnovujem spojenie...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_bot())
