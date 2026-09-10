import os
import time
import asyncio
import pandas as pd
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi

# Flask server pre udržanie živého stavu na Renderi
app = Flask('')

@app.route('/')
def home():
    return "Riobot XAUUSD Trading Engine is running live!"

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

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Chyba: Chýbajú Telegram premenné.")
        return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Chyba pri posielaní na Telegram: {e}")

async def run_bot():
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        print("Chyba: MetaApi token alebo Account ID nie sú nastavené.")
        return

    api = MetaApi(METAAPI_TOKEN)
    
    try:
        account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
        if account.state != 'DEPLOYED':
            await account.deploy()
        
        # Počkáme na pripojenie k terminal API
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()
        
        send_telegram("🚀 *Riobot XAUUSD Advanced Engine bol úspešne spustený a pripojený k MetaApi!*")
        print("Riobot pripojený k MetaApi, začínam analýzu...")

        while True:
            try:
                # 1. Stiahnutie historických sviečok pre 1H (trend) a 5M (vstupy)
                # MetaApi podporuje timeframe napr. '1h', '5m'
                candles_1h = await connection.get_historical_candles(SYMBOL, '1h', 100)
                candles_5m = await connection.get_historical_candles(SYMBOL, '5m', 100)

                if len(candles_1h) > 50 and len(candles_5m) > 50:
                    df_1h = pd.DataFrame(candles_1h)
                    df_5m = pd.DataFrame(candles_5m)

                    # 2. Výpočet EMA 50 a EMA 200 na 1H grafu
                    df_1h['ema_50'] = df_1h['close'].ewm(span=50, adjust=False).mean()
                    df_1h['ema_200'] = df_1h['close'].ewm(span=200, adjust=False).mean()
                    
                    trend_bullish = df_1h['close'].iloc[-1] > df_1h['ema_50'].iloc[-1] > df_1h['ema_200'].iloc[-1]
                    trend_bearish = df_1h['close'].iloc[-1] < df_1h['ema_50'].iloc[-1] < df_1h['ema_200'].iloc[-1]

                    # 3. Filter konsolidácie / bočného pohybu na 5M (podľa rozpätia sviečok / volatility)
                    df_5m['range'] = df_5m['high'] - df_5m['low']
                    avg_range = df_5m['range'].tail(10).mean()
                    
                    # Ak je priemerné rozpätie príliš nízke, trh je v konsolidácii
                    is_consolidating = avg_range < 1.2 # Prah pre XAUUSD

                    if not is_consolidating:
                        # 4. Stochastic a MACD výpočty na 5M
                        # Stochastic %K a %D
                        low_min = df_5m['low'].rolling(window=14).min()
                        high_max = df_5m['high'].rolling(window=14).max()
                        df_5m['stoch_k'] = ((df_5m['close'] - low_min) / (high_max - low_min)) * 100
                        df_5m['stoch_d'] = df_5m['stoch_k'].rolling(window=3).mean()

                        # MACD výpočet
                        exp1 = df_5m['close'].ewm(span=12, adjust=False).mean()
                        exp2 = df_5m['close'].ewm(span=26, adjust=False).mean()
                        df_5m['macd'] = exp1 - exp2
                        df_5m['macd_signal'] = df_5m['macd'].ewm(span=9, adjust=False).mean()

                        current_price = df_5m['close'].iloc[-1]
                        stoch_k = df_5m['stoch_k'].iloc[-1]
                        stoch_d = df_5m['stoch_d'].iloc[-1]
                        macd_val = df_5m['macd'].iloc[-1]
                        macd_sig = df_5m['macd_signal'].iloc[-1]

                        # 5. Podmienka pre BUY signál
                        if trend_bullish and stoch_k < 20 and stoch_k > stoch_d and macd_val > macd_sig:
                            entry = current_price
                            tp = entry + 6.0
                            sl = entry - 10.0
                            msg = f"🟢 *XAUUSD BUY Signal - Riobot*\n\nEntry: {entry:.2f}\nTP (+6$): {tp:.2f}\nSL (-10$): {sl:.2f}\n*Riadenie: BE na +1 pri dosiahnutí +3 zisku*"
                            send_telegram(msg)
                            time.sleep(300) # Pauza po signáli, aby neposielal duplicity

                        # 6. Podmienka pre SELL signál
                        elif trend_bearish and stoch_k > 80 and stoch_k < stoch_d and macd_val < macd_sig:
                            entry = current_price
                            tp = entry - 6.0
                            sl = entry + 10.0
                            msg = f"🔴 *XAUUSD SELL Signal - Riobot*\n\nEntry: {entry:.2f}\nTP (-6$): {tp:.2f}\nSL (+10$): {sl:.2f}\n*Riadenie: BE na +1 pri dosiahnutí +3 zisku*"
                            send_telegram(msg)
                            time.sleep(300)

                # Kontrola trhu každých 60 sekúnd
                await asyncio.sleep(60)

            except Exception as loop_error:
                print(f"Chyba v iterácii cyklu: {loop_error}")
                await asyncio.sleep(15)

    except Exception as e:
        print(f"Chyba v MetaApi spojení: {e}")

if __name__ == "__main__":
    keep_alive()
    # Spustenie asynchrónneho cyklu pre MetaApi
    asyncio.run(run_bot())
