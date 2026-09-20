import os
import time
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests
import pandas as pd

app = Flask('')

@app.route('/')
def home():
    return "Riobot ProCent M15 Active"

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
LOT_SIZE = 0.02
TIMEFRAME = "15m"

TP_POINTS = 600.0       
BE_TRIGGER = 400.0      
BE_LOCK = 150.0         
SL_POINTS = 2000.0      

last_trade_time = 0
COOLDOWN_SECONDS = 60

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

async def main():
    global last_trade_time
    send_telegram("🚀 Riobot sa pripája na ProCent účet...")
    
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
    
    send_telegram("✅ Riobot je úspešne pripojený, stráži M15 a čaká na sviečky!")

    while True:
        try:
            # Kontrola pozícií a break-even
            positions = await connection.get_positions()
            current_time = time.time()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]

            for pos in btc_positions:
                open_price = pos['openPrice']
                current_sl = pos.get('stopLoss', 0)
                price_info = await connection.get_symbol_price(SYMBOL)
                
                if pos['type'] == 'POSITION_TYPE_BUY':
                    bid = price_info.get('bid')
                    if bid and (bid - open_price) >= BE_TRIGGER:
                        target_sl = open_price + BE_LOCK
                        if current_sl < target_sl:
                            await connection.modify_position(
                                positionId=pos['id'], stop_loss=target_sl, take_profit=pos.get('takeProfit', open_price + TP_POINTS)
                            )
                            send_telegram("🔒 BE aktívne (BUY): SL posunutý do zisku!")
                            
                elif pos['type'] == 'POSITION_TYPE_SELL':
                    ask = price_info.get('ask')
                    if ask and (open_price - ask) >= BE_TRIGGER:
                        target_sl = open_price - BE_LOCK
                        if current_sl > target_sl or current_sl == 0:
                            await connection.modify_position(
                                positionId=pos['id'], stop_loss=target_sl, take_profit=pos.get('takeProfit', open_price - TP_POINTS)
                            )
                            send_telegram("🔒 BE aktívne (SELL): SL posunutý do zisku!")

            # Vstupná logika na základe 1 uzavretej sviečky M15
            if len(btc_positions) == 0 and (current_time - last_trade_time) > COOLDOWN_SECONDS:
                candles = await connection.get_historical_candles(SYMBOL, TIMEFRAME, None, 5)
                
                if candles and len(candles) >= 3:
                    df = pd.DataFrame(candles)
                    prev_candle = df.iloc[-2] # Posledná uzavretá sviečka
                    c_open = prev_candle['open']
                    c_close = prev_candle['close']
                    
                    price_info = await connection.get_symbol_price(SYMBOL)
                    ask = price_info.get('ask')
                    bid = price_info.get('bid')

                    if ask and bid:
                        if c_close > c_open: # Zelená sviečka -> BUY
                            sl = ask - SL_POINTS
                            tp = ask + TP_POINTS
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_trade_time = current_time
                            send_telegram("🟢 BTCUSD BUY (0.02) otvorený podľa M15 sviečky.")

                        elif c_close < c_open: # Červená sviečka -> SELL
                            sl = bid + SL_POINTS
                            tp = bid - TP_POINTS
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_trade_time = current_time
                            send_telegram("🔴 BTCUSD SELL (0.02) otvorený podľa M15 sviečky.")

        except Exception as inner_e:
            print(f"Chyba v obchodnej slučke: {inner_e}")

        await asyncio.sleep(20)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
