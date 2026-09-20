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

last_checked_candle_time = None

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

async def main():
    global last_checked_candle_time
    send_telegram("🚀 Riobot sa pripája na ProCent účet a spúšťa M15 logiku...")
    
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
    
    send_telegram("✅ Riobot je online, stráži BTCUSD a čaká na uzavretie sviečky!")

    while True:
        try:
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]

            # 1. Manažment otvorených pozícií (Break-Even)
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

            # 2. Vstupná logika podľa novej uzavretej M15 sviečky
            if len(btc_positions) == 0:
                candles = await connection.get_historical_candles(SYMBOL, TIMEFRAME, None, 3)
                
                if candles and len(candles) >= 2:
                    df = pd.DataFrame(candles)
                    prev_candle = df.iloc[-2] # Posledná reálne uzavretá sviečka
                    candle_time = prev_candle['time']
                    
                    # Obchodujeme iba vtedy, ak ide o novú, doteraz nespracovanú sviečku
                    if last_checked_candle_time != candle_time:
                        c_open = prev_candle['open']
                        c_close = prev_candle['close']
                        
                        price_info = await connection.get_symbol_price(SYMBOL)
                        ask = price_info.get('ask')
                        bid = price_info.get('bid')

                        if ask and bid:
                            if c_close > c_open: # Zelená -> BUY
                                sl = ask - SL_POINTS
                                tp = ask + TP_POINTS
                                await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                last_checked_candle_time = candle_time
                                send_telegram("🟢 BTCUSD BUY (0.02) otvorený podľa uzavretej M15 sviečky.")

                            elif c_close < c_open: # Červená -> SELL
                                sl = bid + SL_POINTS
                                tp = bid - TP_POINTS
                                await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                                last_checked_candle_time = candle_time
                                send_telegram("🔴 BTCUSD SELL (0.02) otvorený podľa uzavretej M15 sviečky.")

        except Exception as inner_e:
            print(f"Chyba v slučke: {inner_e}")

        await asyncio.sleep(10)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
