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
    return "Riobot Safe M5 Engine - Online"

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
    
    send_telegram("🚀 Riobot štartuje (Bezpečný M5 mód)...")
    
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
                
            send_telegram("🚀 Riobot pripojený a stabilne stráži trhy!")
            
            while True:
                try:
                    positions = await connection.get_positions()
                except Exception as pos_err:
                    print(f"Chyba pozícií: {pos_err}")
                    await asyncio.sleep(5)
                    continue
                
                btc_positions = [p for p in positions if p['symbol'] == 'BTCUSD']
                gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']
                
                # --- SPRÁVA BTC ---
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
                                send_telegram(f"🛡️ BTCUSD BUY posunutý do BE (+1)!")
                        elif p_type == 'POSITION_TYPE_SELL':
                            profit = open_price - ask
                            if profit >= 7 and (current_sl > open_price - 1 or current_sl == 0):
                                await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1, take_profit=p['takeProfit'])
                                send_telegram(f"🛡️ BTCUSD SELL posunutý do BE (-1)!")
                    except Exception as e:
                        print(f"BTC správa chyba: {e}")

                # --- SPRÁVA ZLATO ---
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
                                send_telegram(f"🛡️ XAUUSD BUY posunutý do BE (+1.5)!")
                        elif p_type == 'POSITION_TYPE_SELL':
                            profit = open_price - ask
                            if profit >= 6 and (current_sl > open_price - 1.5 or current_sl == 0):
                                await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1.5, take_profit=p['takeProfit'])
                                send_telegram(f"🛡️ XAUUSD SELL posunutý do BE (-1.5)!")
                    except Exception as e:
                        print(f"Gold správa chyba: {e}")

                # --- VSTUPY ---
                if is_safe_to_trade():
                    try:
                        candles_gold = await connection.get_candles('XAUUSD', timeframe='5m', limit=3)
                        candles_btc = await connection.get_candles('BTCUSD', timeframe='5m', limit=3)
                        
                        # Zlato
                        if len(gold_positions) == 0 and len(candles_gold) >= 2:
                            c_prev = candles_gold[-2]
                            c_curr = candles_gold[-1]
                            
                            prev_green = c_prev['close'] > c_prev['open']
                            prev_red = c_prev['close'] < c_prev['open']
                            curr_green = c_curr['close'] > c_curr['open']
                            curr_red = c_curr['close'] < c_curr['open']
                            
                            is_gold_buy = (curr_green and prev_green) or (curr_green and prev_red and c_curr['close'] >= c_prev['open'])
                            is_gold_sell = (curr_red and prev_red) or (curr_red and prev_green and c_curr['close'] <= c_prev['open'])
                            
                            symbol_price = await connection.get_symbol_price('XAUUSD')
                            
                            if is_gold_buy:
                                ask = symbol_price['ask']
                                await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=ask - 14, take_profit=ask + 10)
                                send_telegram(f"🟢 XAUUSD BUY!")
                            elif is_gold_sell:
                                bid = symbol_price['bid']
                                await connection.create_market_sell_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=bid + 14, take_profit=bid - 10)
                                send_telegram(f"🔴 XAUUSD SELL!")

                        # BTC
                        if len(btc_positions) == 0 and len(candles_btc) >= 2:
                            c_prev = candles_btc[-2]
                            c_curr = candles_btc[-1]
                            
                            prev_green = c_prev['close'] > c_prev['open']
                            prev_red = c_prev['close'] < c_prev['open']
                            curr_green = c_curr['close'] > c_curr['open']
                            curr_red = c_curr['close'] < c_curr['open']
                            
                            is_btc_buy = (curr_green and prev_green) or (curr_green and prev_red and c_curr['close'] >= c_prev['open'])
                            is_btc_sell = (curr_red and prev_red) or (curr_red and prev_green and c_curr['close'] <= c_prev['open'])
                            
                            symbol_price = await connection.get_symbol_price('BTCUSD')
                            
                            if is_btc_buy:
                                ask = symbol_price['ask']
                                await connection.create_market_buy_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=ask - 100, take_profit=ask + 150)
                                send_telegram(f"🟢 BTCUSD BUY!")
                            elif is_btc_sell:
                                bid = symbol_price['bid']
                                await connection.create_market_sell_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=bid + 100, take_profit=bid - 150)
                                send_telegram(f"🔴 BTCUSD SELL!")
                                
                    except Exception as candle_err:
                        print(f"Chyba sviečok: {candle_err}")

                await asyncio.sleep(10)
                
        except Exception as e:
            print(f"Hlavná chyba spojenia: {e}")
            send_telegram(f"⚠️ Obnovujem spojenie...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_bot())
