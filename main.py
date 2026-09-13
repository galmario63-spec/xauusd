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
    return "Riobot Advanced Engine - Optimized"

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
COOLDOWN_SECONDS = 120  # Skrátené na 2 minúty pre častejšie príležitosti

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

    # Fibonacci (posledných 50 sviečok)
    recent_high = high.tail(50).max()
    recent_low = low.tail(50).min()
    fib_618 = recent_high - (recent_high - recent_low) * 0.618
    fib_382 = recent_high - (recent_high - recent_low) * 0.382 # Rozšírené pásmo pre Fibo

    return df.iloc[-1], fib_382, fib_618

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
            
            send_telegram("🚀 Riobot beží s optimalizovanou stratégiou (rýchlejší cooldown + voľnejšie filtre)!")

            while True:
                try:
                    positions = await connection.get_positions()
                    current_time = time.time()

                    btc_positions = [p for p in positions if p['symbol'] == SYMBOL]
                    btc_positions_count = len(btc_positions)

                    # Break-Even manažment: zisk +4 -> SL na +1
                    for pos in btc_positions:
                        if pos['type'] == 'POSITION_TYPE_BUY':
                            open_price = pos['openPrice']
                            current_sl = pos.get('stopLoss', 0)
                            
                            price_info = await connection.get_symbol_price(SYMBOL)
                            bid = price_info.get('bid')

                            if bid and (bid - open_price) >= 4.0:
                                target_sl = open_price + 1.0
                                if current_sl < target_sl:
                                    await connection.modify_position(
                                        positionId=pos['id'],
                                        stop_loss=target_sl,
                                        take_profit=pos.get('takeProfit', open_price + 10.0)
                                    )
                                    send_telegram("🔒 BE aktívne: SL posunutý na +1!")

                    # Vstupná logika s optimalizovanými filtrami
                    if btc_positions_count == 0 and (current_time - last_trade_time) > COOLDOWN_SECONDS:
                        candles = await connection.get_historical_candles(SYMBOL, TIMEFRAME, None, 200)
                        
                        if candles and len(candles) > 200:
                            latest, fib_low, fib_high = calculate_indicators(candles)
                            price_info = await connection.get_symbol_price(SYMBOL)
                            ask = price_info.get('ask')

                            if ask:
                                # Optimalizované podmienky pre BUY:
                                # 1. Trend: Cena nad EMA 50 (alebo veľmi blízko)
                                # 2. Momentum: MACD rastie alebo je nad signálom
                                # 3. Stochastic: Nie je v silnej prekúpenej zóne (< 85)
                                trend_ok = ask >= (latest['ema50'] * 0.999)
                                momentum_ok = latest['macd'] >= latest['macd_signal']
                                stoch_ok = latest['stoch_k'] < 85

                                if trend_ok and momentum_ok and stoch_ok:
                                    sl = ask - 8.0
                                    tp = ask + 10.0
                                    
                                    await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                    last_trade_time = current_time
                                    send_telegram(f"🎯 Riobot našiel príležitosť! Otvoril BUY (SL -8, TP +10).")

                except Exception as inner_e:
                    print(f"Chyba v obchodnej slučke: {inner_e}")

                await asyncio.sleep(10)

        except Exception as e:
            print(f"Chyba pripojenia: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
