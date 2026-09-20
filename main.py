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
    return "Riobot Exact SAR Active"

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

SYMBOL = "BTC"
LOT_SIZE = 0.30         
TP_POINTS = 600.0       # 3 € cieľ
BE_TRIGGER = 250.0      
BE_LOCK = 100.0         
SL_POINTS = 1500.0      

SAR_STEP = 0.80
SAR_MAX = 0.40

last_signal_candle = None
startup_message_sent = False

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_exact_sar_signal(candles):
    if not candles or len(candles) < 15:
        return None
    
    highs = [c['high'] for c in candles]
    lows = [c['low'] for c in candles]
    closes = [c['close'] for c in candles]
    
    n = len(candles)
    sar = [0.0] * n
    bullish = highs[1] > highs[0]
    
    if bullish:
        sar[1] = lows[0]
        ep = highs[1]
    else:
        sar[1] = highs[0]
        ep = lows[1]
        
    af = SAR_STEP
    
    for i in range(2, n):
        prev_sar = sar[i-1]
        if bullish:
            temp_sar = prev_sar + af * (ep - prev_sar)
            temp_sar = min(temp_sar, lows[i-1], lows[max(0, i-2)])
            
            if highs[i] > ep:
                ep = highs[i]
                af = min(af + SAR_STEP, SAR_MAX)
            
            if lows[i] < temp_sar:
                bullish = False
                sar[i] = ep
                ep = lows[i]
                af = SAR_STEP
            else:
                bullish = True
                sar[i] = temp_sar
        else:
            temp_sar = prev_sar + af * (ep - prev_sar)
            temp_sar = max(temp_sar, highs[i-1], highs[max(0, i-2)])
            
            if lows[i] < ep:
                ep = lows[i]
                af = min(af + SAR_STEP, SAR_MAX)
            
            if highs[i] > temp_sar:
                bullish = True
                sar[i] = ep
                ep = highs[i]
                af = SAR_STEP
            else:
                bullish = False
                sar[i] = temp_sar

    # Vráti signál hneď, ako je trend v stave BUY alebo SELL
    return "BUY" if bullish else "SELL"

async def main():
    global last_signal_candle, startup_message_sent
    
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
    
    active_symbol = SYMBOL
    try:
        prices = await connection.get_symbol_price(SYMBOL)
        if not prices:
            active_symbol = "BTCUSD"
    except Exception:
        active_symbol = "BTCUSD"

    if not startup_message_sent:
        send_telegram("🚀 Riobot Exact SAR pripravený na bodky.")
        startup_message_sent = True

    while True:
        try:
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] in [active_symbol, "BTC", "BTCUSD"]]

            price_info = await connection.get_symbol_price(active_symbol)
            ask = price_info.get('ask')
            bid = price_info.get('bid')

            # Break-Even manažment
            for pos in btc_positions:
                open_price = pos['openPrice']
                current_sl = pos.get('stopLoss', 0)
                
                if pos['type'] == 'POSITION_TYPE_BUY' and bid:
                    if (bid - open_price) >= BE_TRIGGER:
                        target_sl = open_price + BE_LOCK
                        if current_sl < target_sl:
                            await connection.modify_position(
                                positionId=pos['id'], stop_loss=target_sl, take_profit=pos.get('takeProfit', open_price + TP_POINTS)
                            )
                            send_telegram("🔒 BE aktívne (BUY)")
                            
                elif pos['type'] == 'POSITION_TYPE_SELL' and ask:
                    if (open_price - ask) >= BE_TRIGGER:
                        target_sl = open_price - BE_LOCK
                        if current_sl > target_sl or current_sl == 0:
                            await connection.modify_position(
                                positionId=pos['id'], stop_loss=target_sl, take_profit=pos.get('takeProfit', open_price - TP_POINTS)
                            )
                            send_telegram("🔒 BE aktívne (SELL)")

            # Vstupy hneď pri detekcii bodky
            if len(btc_positions) == 0 and ask and bid:
                try:
                    candles = await connection.get_historical_candles(active_symbol, "1m", None, 30)
                except Exception:
                    candles = None
                
                if candles and len(candles) >= 15:
                    current_candle_time = candles[-1].get('time')
                    
                    if last_signal_candle != current_candle_time:
                        signal = get_exact_sar_signal(candles)
                        
                        if signal == "BUY":
                            sl = ask - SL_POINTS
                            tp = ask + TP_POINTS
                            await connection.create_market_buy_order(active_symbol, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_signal_candle = current_candle_time
                            send_telegram("🟢 Exact SAR BUY (0.30 Lot) otvorený.")
                            await asyncio.sleep(10)
                            
                        elif signal == "SELL":
                            sl = bid + SL_POINTS
                            tp = bid - TP_POINTS
                            await connection.create_market_sell_order(active_symbol, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_signal_candle = current_candle_time
                            send_telegram("🔴 Exact SAR SELL (0.30 Lot) otvorený.")
                            await asyncio.sleep(10)

            await asyncio.sleep(2)

        except Exception as inner_e:
            print(f"Chyba: {inner_e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
