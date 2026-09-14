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
    return "Riobot Stochastic Engine - Online"

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

def align_stochastic(candles, k_period=14, d_period=3):
    if len(candles) < k_period:
        return 50, 50
    
    closes = [c['close'] for c in candles]
    highs = [c['high'] for c in candles]
    lows = [c['low'] for c in candles]
    
    k_values = []
    for i in range(len(candles) - d_period + 1):
        window_highs = highs[i:i+k_period]
        window_lows = lows[i:i+k_period]
        highest_high = max(window_highs)
        lowest_low = min(window_lows)
        
        current_close = closes[i + k_period - 1]
        if highest_high == lowest_low:
            k = 50
        else:
            k = (current_close - lowest_low) / (highest_high - lowest_low) * 100
        k_values.append(k)
        
    current_k = k_values[-1] if k_values else 50
    current_d = sum(k_values[-d_period:]) / len(k_values[-d_period:]) if len(k_values) >= d_period else current_k
    
    return current_k, current_d

async def run_bot():
    keep_alive()
    
    send_telegram("🚀 Riobot štartuje (Opravený Stochastic filter)...")
    
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
                
            send_telegram("🚀 Riobot pripojený a beží!")
            
            while True:
                positions = await connection.get_positions()
                
                btc_positions = [p for p in positions if p['symbol'] == 'BTCUSD']
                gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']
                
                # --- SPRÁVA BTC (BE pri +7 na +1) ---
                for p in btc_positions:
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

                # --- SPRÁVA ZLATO (BE pri +1.5 na +1) ---
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

                # --- VSTUPY: SVIEČKY + STOCHASTIC ---
                try:
                    candles_gold = await connection.get_candles('XAUUSD', timeframe='5m', limit=25)
                    candles_btc = await connection.get_candles('BTCUSD', timeframe='5m', limit=25)
                    
                    # Zlato vstup
                    if len(gold_positions) == 0 and len(candles_gold) >= 20:
                        c1, c2 = candles_gold[-1], candles_gold[-2]
                        is_green_1, is_green_2 = c1['close'] > c1['open'], c2['close'] > c2['open']
                        is_red_1, is_red_2 = c1['close'] < c1['open'], c2['close'] < c2['open']
                        
                        k_g, d_g = align_stochastic(candles_gold)
                        symbol_price = await connection.get_symbol_price('XAUUSD')
                        
                        if is_green_1 and is_green_2 and k_g < 80:
                            ask = symbol_price['ask']
                            await connection.create_market_buy_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=ask - 12, take_profit=ask + 10)
                            send_telegram(f"🟢 XAUUSD BUY (Sviečky + Stoch K:{k_g:.1f})!")
                        elif is_red_1 and is_red_2 and k_g > 20:
                            bid = symbol_price['bid']
                            await connection.create_market_sell_order(symbol='XAUUSD', volume=GOLD_LOT, stop_loss=bid + 12, take_profit=bid - 10)
                            send_telegram(f"🔴 XAUUSD SELL (Sviečky + Stoch K:{k_g:.1f})!")

                    # BTC vstup
                    if len(btc_positions) == 0 and len(candles_btc) >= 20:
                        c1, c2 = candles_btc[-1], candles_btc[-2]
                        is_green_1, is_green_2 = c1['close'] > c1['open'], c2['close'] > c2['open']
                        is_red_1, is_red_2 = c1['close'] < c1['open'], c2['close'] < c2['open']
                        
                        k_b, d_b = align_stochastic(candles_btc)
                        symbol_price = await connection.get_symbol_price('BTCUSD')
                        
                        if is_green_1 and is_green_2 and k_b < 80:
                            ask = symbol_price['ask']
                            await connection.create_market_buy_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=ask - 100, take_profit=ask + 150)
                            send_telegram(f"🟢 BTCUSD BUY (Sviečky + Stoch K:{k_b:.1f})!")
                        elif is_red_1 and is_red_2 and k_b > 20:
                            bid = symbol_price['bid']
                            await connection.create_market_sell_order(symbol='BTCUSD', volume=BTC_LOT, stop_loss=bid + 100, take_profit=bid - 150)
                            send_telegram(f"🔴 BTCUSD SELL (Sviečky + Stoch K:{k_b:.1f})!")
                            
                except Exception as candle_err:
                    print(f"Chyba sviečok/Stoch: {candle_err}")

                await asyncio.sleep(15)
                
        except Exception as e:
            print(f"Chyba spojenia: {e}")
            send_telegram(f"⚠️ Obnovujem spojenie...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_bot())
