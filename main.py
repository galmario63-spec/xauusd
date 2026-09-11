import os
import time
import asyncio
import pandas as pd
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests

# Flask server pre udržanie živého stavu
app = Flask('')

@app.route('/')
def home():
    return "Riobot XAUUSD Auto-Trading Engine is running live!"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# Konfigurácia z Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = "XAUUSD"
LOT_SIZE = 0.01

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Chyba Telegram: {e}")

async def manage_open_trades(connection):
    """Sleduje otvorené obchody a ak zisk dosiahne +3, posunie SL na Break-Even (+1)"""
    try:
        positions = await connection.get_positions()
        for pos in positions:
            if pos['symbol'] == SYMBOL:
                profit = pos.get('profit', 0)
                open_price = pos.get('openPrice', 0)
                sl = pos.get('stopLoss', 0)
                ticket = pos.get('id')

                # Ak je to BUY obchod
                if pos['type'] == 'POSITION_TYPE_BUY':
                    target_be = open_price + 3.0
                    if profit >= 3.0 and sl < open_price + 1.0:
                        await connection.modify_position(
                            position_id=ticket,
                            stop_loss=open_price + 1.0,
                            take_profit=pos.get('takeProfit', 0)
                        )
                        send_telegram(f"🛡️ *BREAK-EVEN*🟢 BUY obchod {ticket} posunutý na BE (+1).")

                # Ak je to SELL obchod
                elif pos['type'] == 'POSITION_TYPE_SELL':
                    target_be = open_price - 3.0
                    if profit >= 3.0 and (sl > open_price - 1.0 or sl == 0):
                        await connection.modify_position(
                            position_id=ticket,
                            stop_loss=open_price - 1.0,
                            take_profit=pos.get('takeProfit', 0)
                        )
                        send_telegram(f"🛡️ *BREAK-EVEN*🔴 SELL obchod {ticket} posunutý na BE (+1).")
    except Exception as e:
        print(f"Chyba v manage_open_trades: {e}")

async def run_bot():
    print("Riobot štartuje pripojenie na MetaApi...")
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    # Čakanie na deploy účtu
    original_state = account.state
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    print("Čakám na pripojenie k MetaTrader API...")
    await account.wait_connected()
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    print("MetaApi je plne pripojené a synchronizované!")
    send_telegram("🚀 *Riobot pre XAUUSD bol úspešne spustený a obchoduje!*")

    while True:
        try:
            # Správa otvorených pozícií (Break-Even)
            await manage_open_trades(connection)

            # Sťahovanie sviečok (M5)
            candles = await connection.get_candles(SYMBOL, timeframe='5m', count=100)
            df = pd.DataFrame(candles)

            if df.empty or len(df) < 200:
                print("Nedostatok dát, čakám...")
                await asyncio.sleep(30)
                continue

            # Výpočet indikátorov (EMA, Stochastic, MACD)
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

            # Stochastic Oscillator (14, 3, 3)
            low_min = df['low'].rolling(window=14).min()
            high_max = df['high'].rolling(window=14).max()
            df['stoch_k'] = ((df['close'] - low_min) / (high_max - low_min)) * 100
            df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()

            # MACD
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

            # Aktuálne hodnoty poslednej uzavretej/prebiehajúcej sviečky
            last = df.iloc[-2]
            current_price = df.iloc[-1]['close']

            trend_bullish = last['ema50'] > last['ema200']
            trend_bearish = last['ema50'] < last['ema200']
            stoch_k = last['stoch_k']
            stoch_d = last['stoch_d']
            macd_val = last['macd']
            macd_sig = last['macd_signal']

            # Kontrola existujúcich pozícií pre daný symbol
            positions = await connection.get_positions()
            symbol_positions = [p for p in positions if p['symbol'] == SYMBOL]

            # Obchodujeme iba ak nemáme otvorenú pozíciu
            if len(symbol_positions) == 0:
                # BUY PODMIENKA
                if trend_bullish and stoch_k < 20 and stoch_k > stoch_d and macd_val > macd_sig:
                    entry = current_price
                    tp = entry + 6.0
                    sl = entry - 10.0
                    
                    result = await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)
                    msg = f"🟢 *XAUUSD BUY Obchod otvorený!*\nCena: {entry:.2f}\nTP (+6$): {tp:.2f}\nSL (-10$): {sl:.2f}"
                    send_telegram(msg)
                    print(msg)

                # SELL PODMIENKA
                elif trend_bearish and stoch_k > 80 and stoch_k < stoch_d and macd_val < macd_sig:
                    entry = current_price
                    tp = entry - 6.0
                    sl = entry + 10.0
                    
                    result = await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp)
                    msg = f"🔴 *XAUUSD SELL Obchod otvorený!*\nCena: {entry:.2f}\nTP (-6$): {tp:.2f}\nSL (+10$): {sl:.2f}"
                    send_telegram(msg)
                    print(msg)

            await asyncio.sleep(30)

        except Exception as loop_error:
            print(f"Chyba v cykle: {loop_error}")
            await asyncio.sleep(15)

except Exception as e:
    print(f"Chyba MetaApi: {e}")

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
