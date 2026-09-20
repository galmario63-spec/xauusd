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
    return "Riobot Smart Active"

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
TP_POINTS = 600.0       
BE_TRIGGER = 400.0      
BE_LOCK = 150.0         
SL_POINTS = 2000.0      

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
    global startup_message_sent
    
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
    
    if not startup_message_sent:
        send_telegram("🚀 Riobot beží s vyhodnotením smeru.")
        startup_message_sent = True

    while True:
        try:
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]

            # 1. Break-Even manažment
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
                            send_telegram("🔒 BE aktívne (BUY)")
                            
                elif pos['type'] == 'POSITION_TYPE_SELL':
                    ask = price_info.get('ask')
                    if ask and (open_price - ask) >= BE_TRIGGER:
                        target_sl = open_price - BE_LOCK
                        if current_sl > target_sl or current_sl == 0:
                            await connection.modify_position(
                                positionId=pos['id'], stop_loss=target_sl, take_profit=pos.get('takeProfit', open_price - TP_POINTS)
                            )
                            send_telegram("🔒 BE aktívne (SELL)")

            # 2. Vyhodnotenie smeru a vstup
            if len(btc_positions) == 0:
                candles = await connection.get_historical_candles(SYMBOL, "1h", None, 2)
                price_info = await connection.get_symbol_price(SYMBOL)
                ask = price_info.get('ask')
                bid = price_info.get('bid')
                
                if candles and len(candles) >= 1 and ask and bid:
                    last_c = candles[-1]
                    
                    if last_c['close'] >= last_c['open']:
                        sl = ask - SL_POINTS
                        tp = ask + TP_POINTS
                        await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                        send_telegram("🟢 BTCUSD BUY otvorený na základe trendu.")
                    else:
                        sl = bid + SL_POINTS
                        tp = bid - TP_POINTS
                        await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                        send_telegram("🔴 BTCUSD SELL otvorený na základe trendu.")
                        
                    await asyncio.sleep(60)

            await asyncio.sleep(10)

        except Exception as inner_e:
            print(f"Chyba: {inner_e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
