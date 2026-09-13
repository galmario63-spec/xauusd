import os
import time
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests
import pandas as pd
import numpy as np

app = Flask('')

@app.route('/')
def home():
    return "Riobot Advanced Engine - Obojstranný režim (BUY & SELL)"

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
LOT_SIZE = 0.1
TIMEFRAME = "5m"

last_trade_time = 0
COOLDOWN_SECONDS = 120  # 2 minúty pauza medzi obchodmi

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def calculate_indicators(candles):
    df = pd.DataFrame(candles)
    close = df['close']
    high = df['high']
    low = df['low']

    # EMA 50 a 200
    df['ema50'] = close.ewm(span=50, adjust=False).mean()
    df['ema200'] = close.ewm(span=200, adjust=False).mean()

    # MACD
    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # Stochastic (14, 3, 3)
    low_14 = low.rolling(window=14).min()
    high_14 = high.rolling(window=14).max()
    df['stoch_k'] = 100 * ((close - low_14) / (high_14 - low_14))
    df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()

    return df.iloc[-1]

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
            
            await connection.connect()
            await connection.wait_synchronized()
            
            send_telegram("🚀 Riobot beží v obojstrannom režime (BUY & SELL aktívne)!")

            while True:
                try:
                    positions = await connection.get_positions()
                    current_time = time.time()

                    btc_positions = [p for p in positions if p['symbol'] == SYMBOL]
                    btc_positions_count = len(btc_positions)

                    # Break-Even manažment pre BUY aj SELL
                    for pos in btc_positions:
                        open_price = pos['openPrice']
                        current_sl = pos.get('stopLoss', 0)
                        price_info = await connection.get_symbol_price(SYMBOL)
                        
                        if pos['type'] == 'POSITION_TYPE_BUY':
                            bid = price_info.get('bid')
                            if bid and (bid - open_price) >= 4.0:
                                target_sl = open_price + 1.0
                                if current_sl < target_sl:
                                    await connection.modify_position(
                                        positionId=pos['id'],
                                        stop_loss=target_sl,
                                        take_profit=pos.get('takeProfit', open_price + 10.0)
                                    )
                                    send_telegram("🔒 BE aktívne (BUY): SL posunutý na +1!")
                                    
                        elif pos['type'] == 'POSITION_TYPE_SELL':
                            ask = price_info.get('ask')
                            if ask and (open_price - ask) >= 4.0:
                                target_sl = open_price - 1.0
                                if current_sl > target_sl or current_sl == 0:
                                    await connection.modify_position(
                                        positionId=pos['id'],
                                        stop_loss=target_sl,
                                        take_profit=pos.get('takeProfit', open_price - 10.0)
                                    )
                                    send_telegram("🔒 BE aktívne (SELL): SL posunutý na +1!")

                    # Obojstranná vstupná logika
                    if btc_positions_count == 0 and (current_time - last_trade_time) > COOLDOWN_SECONDS:
                        candles = await connection.get_historical_candles(SYMBOL, TIMEFRAME, None, 200)
                        
                        if candles and len(candles) > 200:
                            latest = calculate_indicators(candles)
                            price_info = await connection.get_symbol_price(SYMBOL)
                            ask = price_info.get('ask')
                            bid = price_info.get('bid')

                            if ask and bid:
                                # Podmienky pre BUY
                                buy_trend = ask >= (latest['ema50'] * 0.999)
                                buy_momentum = latest['macd'] >= latest['macd_signal']
                                buy_stoch = latest['stoch_k'] < 85

                                # Podmienky pre SELL (obrat na vrchole)
                                sell_momentum = latest['macd'] <= latest['macd_signal']
                                sell_stoch = latest['stoch_k'] > 85

                                if buy_trend and buy_momentum and buy_stoch:
                                    sl = ask - 8.0
                                    tp = ask + 10.0
                                    await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                    last_trade_time = current_time
                                    send_telegram("🎯 Riobot otvoril BUY (Trend pokračuje).")

                                elif sell_stoch and sell_momentum:
                                    sl = bid + 8.0
                                    tp = bid - 10.0
                                    await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                    last_trade_time = current_time
                                    send_telegram("🎯 Riobot otvoril SELL (Obrat na vrchole).")

                except Exception as inner_e:
                    print(f"Chyba v obchodnej slučke: {inner_e}")

                await asyncio.sleep(10)

        except Exception as e:
            print(f"Chyba pripojenia: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
