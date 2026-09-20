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
    return "Riobot M1 Solid Active"

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
LOT_SIZE = 0.10
TP_POINTS = 300.0       
BE_TRIGGER = 200.0      
BE_LOCK = 80.0          
SL_POINTS = 1500.0      
MIN_WICK = 20.0         # Minimálna veľkosť knôtu v bodoch

last_processed_candle = None
startup_message_sent = False

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

async def main():
    global last_processed_candle, startup_message_sent
    
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
    
    if not startup_message_sent:
        send_telegram("🚀 Riobot M1 stabilný režim zapnutý.")
        startup_message_sent = True

    while True:
        try:
            # 1. Kontrola otvorených pozícií a Break-Even manažment
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]

            price_info = await connection.get_symbol_price(SYMBOL)
            ask = price_info.get('ask')
            bid = price_info.get('bid')

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

            # 2. Vstupy na M1 ak nie je otvorená pozícia
            if len(btc_positions) == 0 and ask and bid:
                candles = await connection.get_historical_candles(SYMBOL, "1m", None, 3)
                
                if candles and len(candles) >= 2:
                    curr_c = candles[-2] # Berieme poslednú uzavretú M1 sviečku
                    c_time = curr_c.get('time')
                    
                    if last_processed_candle != c_time:
                        op = curr_c['open']
                        cl = curr_c['close']
                        high = curr_c['high']
                        low = curr_c['low']
                        
                        upper_wick = high - max(op, cl)
                        lower_wick = min(op, cl) - low
                        
                        # Logika pre BUY (odmietnutie dole / dolný knôt)
                        if lower_wick >= MIN_WICK_POINTS and lower_wick > upper_wick:
                            sl = ask - SL_POINTS
                            tp = ask + TP_POINTS
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_processed_candle = c_time
                            send_telegram("🟢 M1 BUY (Dolný knôt / Pullback)")
                            await asyncio.sleep(15)
                            
                        # Logika pre SELL (odmietnutie hore / horný knôt)
                        elif upper_wick >= MIN_WICK_POINTS and upper_wick > lower_wick:
                            sl = bid + SL_POINTS
                            tp = bid - TP_POINTS
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_processed_candle = c_time
                            send_telegram("🔴 M1 SELL (Horný knôt / Pullback)")
                            await asyncio.sleep(15)

            await asyncio.sleep(5)

        except Exception as inner_e:
            print(f"Chyba v cykle: {inner_e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
