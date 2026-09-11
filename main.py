import os
import time
import asyncio
import pandas as pd
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi

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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = "XAUUSD"
LOT_SIZE = 0.01  # Zmeň si podľa potreby (napr. 0.01, 0.1)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
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
                open_price = pos.get('openPrice')
                sl = pos.get('stopLoss', 0)
                ticket = pos['id']
                
                # Ak je to BUY obchod a zisk je >= 3.0, posuň SL na open_price + 1.0 (BE +1)
                if pos['type'] == 'POSITION_TYPE_BUY':
                    target_be = open_price + 1.0
                    if profit >= 3.0 and sl < target_be:
                        await connection.modify_position(
                            position_id=ticket,
                            stop_loss=target_be,
                            take_profit=pos.get('takeProfit')
                        )
                        send_telegram(f"🛡️ *Break-Even aktivovaný!* BUY obchod {ticket} posunutý na BE (+1).")

                # Ak je to SELL obchod a zisk je >= 3.0, posuň SL na open_price - 1.0 (BE +1)
                elif pos['type'] == 'POSITION_TYPE_SELL':
                    target_be = open_price - 1.0
                    if profit >= 3.0 and (sl > target_be or sl == 0):
                        await connection.modify_position(
                            position_id=ticket,
                            stop_loss=target_be,
                            take_profit=pos.get('takeProfit')
                        )
                        send_telegram(f"🛡️ *Break-Even aktivovaný!* SELL obchod {ticket} posunutý na BE (+1).")
    except Exception as e:
        print(f"Chyba pri správe pozícií: {e}")

async def run_bot():
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        print("Chyba: Chýbajú MetaApi premenné.")
        return

    api = MetaApi(METAAPI_TOKEN)
    
    try:
        account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
        if account.state != 'DEPLOYED':
            await account.deploy()
        
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()
        
        send_telegram("🚀 *Riobot XAUUSD Auto-Trading Engine spustený a obchoduje naostro!*")
        print("Riobot pripojený k MetaApi, auto-trading aktívny...")

        while True:
            try:
                # 1. Pravidelná kontrola otvorených pozícií (Break-Even manažment)
                await manage_open_trades(connection)

                # 2. Stiahnutie sviečok pre analýzu
                candles_1h = await connection.get_historical_candles(SYMBOL, '1h', 100)
                candles_5m = await connection.get_historical_candles(SYMBOL, '5m', 100)

                if len(candles_1h) > 50 and len(candles_5m) > 50:
                    df_1h = pd.DataFrame(candles_1h)
                    df_5m = pd.DataFrame(candles_5m)

                    # Trend H1 (EMA 50 / 200)
                    df_1h['ema_50'] = df_1h['close'].ewm(span=50, adjust=False).mean()
                    df_1h['ema_200'] = df_1h['close'].ewm(span=200, adjust=False).mean()
                    
                    trend_bullish = df_1h['close'].iloc[-1] > df_1h['ema_50'].iloc[-1] > df_1h['ema_200'].iloc[-1]
                    trend_bearish = df_1h['close'].iloc[-1] < df_1h['ema_50'].iloc[-1] < df_1h['ema_200'].iloc[-1]

                    # Filter konsolidácie na 5M
                    df_5m['range'] = df_5m['high'] - df_5m['low']
                    avg_range = df_5m['range'].tail(10).mean()
                    is_consolidating = avg_range < 1.2

                    if not is_consolidating:
                        # Indikátory na 5M (Stochastic & MACD)
                        low_min = df_5m['low'].rolling(window=14).min()
                        high_max = df_5m['high'].rolling(window=14).max()
                        df_5m['stoch_k'] = ((df_5m['close'] - low_min) / (high_max - low_min)) * 100
                        df_5m['stoch_d'] = df_5m['stoch_k'].rolling(window=3).mean()

                        exp1 = df_5m['close'].ewm(span=12, adjust=False).mean()
                        exp2 = df_5m['close'].ewm(span=26, adjust=False).mean()
                        df_5m['macd'] = exp1 - exp2
                        df_5m['macd_signal'] = df_5m['macd'].ewm(span=9, adjust=False).mean()

                        current_price = df_5m['close'].iloc[-1]
                        stoch_k = df_5m['stoch_k'].iloc[-1]
                        stoch_d = df_5m['stoch_d'].iloc[-1]
                        macd_val = df_5m['macd'].iloc[-1]
                        macd_sig = df_5m['macd_signal'].iloc[-1]

                        # Skontrolujeme, či už nemáme otvorenú pozíciu, aby sme neotvárali duplicity
                        positions = await connection.get_positions()
                        has_open_position = any(p['symbol'] == SYMBOL for p in positions)

                        if not has_open_position:
                            # BUY OBCHOD
                            if trend_bullish and stoch_k < 20 and stoch_k > stoch_d and macv_val > macd_sig if 'macv_val' in locals() else macd_val > macd_sig:
                                entry = current_price
                                tp = entry + 6.0
                                sl = entry - 10.0
                                
                                # Odošle reálny príkaz do MT5
                                result = await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)
                                msg = f"🟢 *XAUUSD BUY Obchod otvorený!*\n\nEntry: {entry:.2f}\nTP (+6$): {tp:.2f}\nSL (-10$): {sl:.2f}"
                                send_telegram(msg)
                                await asyncio.sleep(300)

                            # SELL OBCHOD
                            elif trend_bearish and stoch_k > 80 and stoch_k < stoch_d and macd_val < macd_sig:
                                entry = current_price
                                tp = entry - 6.0
                                sl = entry + 10.0
                                
                                # Odošle reálny príkaz do MT5
                                result = await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp)
                                msg = f"🔴 *XAUUSD SELL Obchod otvorený!*\n\nEntry: {entry:.2f}\nTP (-6$): {tp:.2f}\nSL (+10$): {sl:.2f}"
                                send_telegram(msg)
                                await asyncio.sleep(300)

                await asyncio.sleep(30)

            except Exception as loop_error:
                print(f"Chyba v cykle: {loop_error}")
                await asyncio.sleep(15)

    except Exception as e:
        print(f"Chyba MetaApi: {e}")

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
