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
    return "Riobot XAUUSD Full Smart Engine"

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
LOT_SIZE = 0.01

last_trade_time = 0
COOLDOWN_SECONDS = 900  # 15 minút pauza

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: 
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def calculate_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_macd(prices):
    if len(prices) < 26:
        return 0, 0
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    macd_line = ema12 - ema26
    return macd_line, 0

def calculate_stochastic(highs, lows, closes, period=14):
    if len(closes) < period:
        return 50
    lowest_low = min(lows[-period:])
    highest_high = max(highs[-period:])
    if highest_high == lowest_low:
        return 50
    k = 100 * ((closes[-1] - lowest_low) / (highest_high - lowest_low))
    return k

async def run_bot():
    global last_trade_time
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    await account.wait_connected()
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    send_telegram("Riobot kompletne aktivovaný: EMA, MACD, Stoch, Fib, SL 15, TP 10, BE pri +3!")

    while True:
        try:
            positions = await connection.get_positions()
            
            # Break-Even manažment
            for pos in positions:
                if pos['symbol'] == SYMBOL:
                    op = pos.get('openPrice', 0)
                    current_sl = pos.get('stopLoss', 0)
                    price_info = await connection.get_symbol_price(SYMBOL)
                    
                    if pos['type'] == 'POSITION_TYPE_BUY':
                        bid = price_info.get('bid', 0)
                        if bid and op and (bid - op >= 3.0) and current_sl < (op + 1.5):
                            new_sl = op + 1.5
                            await connection.modify_position(pos['id'], stop_loss=new_sl, take_profit=pos.get('takeProfit', 0))
                            send_telegram("BUY BE aktivovaný! SL na +1.5")
                            
                    elif pos['type'] == 'POSITION_TYPE_SELL':
                        ask = price_info.get('ask', 0)
                        if ask and op and (op - ask >= 3.0) and (current_sl > (op - 1.5) or current_sl == 0):
                            new_sl = op - 1.5
                            await connection.modify_position(pos['id'], stop_loss=new_sl, take_profit=pos.get('takeProfit', 0))
                            send_telegram("SELL BE aktivovaný! SL na +1.5")

            symbol_positions = [p for p in positions if p['symbol'] == SYMBOL]
            current_time = time.time()

            if len(symbol_positions) == 0 and (current_time - last_trade_time >= COOLDOWN_SECONDS):
                candles = await connection.get_candles(SYMBOL, '15m', 50)
                if len(candles) >= 50:
                    closes = [c['close'] for c in candles]
                    highs = [c['high'] for c in candles]
                    lows = [c['low'] for c in candles]
                    
                    # Indikátory
                    ema50 = calculate_ema(closes, 50)
                    ema200 = calculate_ema(closes, 200)
                    macd, _ = calculate_macd(closes)
                    stoch_k = calculate_stochastic(highs, lows, closes)
                    
                    # Fibonacci
                    max_high = max(highs)
                    min_low = min(lows)
                    diff = max_high - min_low
                    fib_618 = max_high - (diff * 0.618)
                    fib_382 = min_low + (diff * 0.382)
                    
                    price_info = await connection.get_symbol_price(SYMBOL)
                    bid = price_info.get('bid')
                    ask = price_info.get('ask')
                    
                    if ask and bid:
                        current_price = ask
                        
                        # Podmienky pre BUY: Cena pod Fib 618, EMA50 nad EMA200, MACD > 0, Stoch < 80 (nie je prekuúpený)
                        is_buy_signal = (current_price <= fib_618) and (ema50 > ema200) and (macd > 0) and (stoch_k < 80)
                        
                        # Podmienky pre SELL: Cena nad Fib 382, EMA50 pod EMA200, MACD < 0, Stoch > 20 (nie je predpredaný)
                        is_sell_signal = (current_price >= fib_382) and (ema50 < ema200) and (macd < 0) and (stoch_k > 20)
                        
                        if is_buy_signal:
                            tp = ask + 10.0
                            sl = ask - 15.0
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp)
                            last_trade_time = time.time()
                            send_telegram(f"XAUUSD BUY (Smart Signal)\nCena: {ask}\nTP: {tp:.2f}\nSL: {sl:.2f}")
                            
                        elif is_sell_signal:
                            tp = bid - 10.0
                            sl = bid + 15.0
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp)
                            last_trade_time.time() if hasattr(last_trade_time, 'time') else setattr(globals(), 'last_trade_time', time.time())
                            last_trade_time = time.time()
                            send_telegram(f"XAUUSD SELL (Smart Signal)\nCena: {bid}\nTP: {tp:.2f}\nSL: {sl:.2f}")

            await asyncio.sleep(30)
        except Exception as e:
            print(f"Chyba: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(run_bot())
