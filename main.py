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
    return "Riobot Advanced Engine - Online"

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

LOT_SIZE = 0.1

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

async def run_bot():
    keep_alive()
    
    send_telegram("🚀 Riobot sa inicializuje (Priamy režim)...")
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
        
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
        
    send_telegram("🚀 Riobot pripojený a pripravený na obchody!")
    
    while True:
        try:
            positions = await connection.get_positions()
            
            btc_positions = [p for p in positions if p['symbol'] == 'BTCUSD']
            gold_positions = [p for p in positions if p['symbol'] == 'XAUUSD']
            
            # Správa BTC pozícií a BE
            for p in btc_positions:
                open_price = p['openPrice']
                p_type = p['type']
                current_sl = p.get('stopLoss', 0)
                symbol_price = await connection.get_symbol_price('BTCUSD')
                bid = symbol_price['bid']
                ask = symbol_price['ask']
                
                if p_type == 'POSITION_TYPE_BUY':
                    if (bid - open_price) >= 300 and current_sl < open_price:
                        await connection.modify_position(position_id=p['id'], stop_loss=open_price, take_profit=p['takeProfit'])
                        send_telegram(f"🛡️ BTCUSD BUY posunutý do BE!")
                elif p_type == 'POSITION_TYPE_SELL':
                    if (open_price - ask) >= 300 and (current_sl > open_price or current_sl == 0):
                        await connection.modify_position(position_id=p['id'], stop_loss=open_price, take_profit=p['takeProfit'])
                        send_telegram(f"🛡️ BTCUSD SELL posunutý do BE!")

            # Správa XAUUSD pozícií a BE
            for p in gold_positions:
                open_price = p['openPrice']
                p_type = p['type']
                current_sl = p.get('stopLoss', 0)
                symbol_price = await connection.get_symbol_price('XAUUSD')
                bid = symbol_price['bid']
                ask = symbol_price['ask']
                
                if p_type == 'POSITION_TYPE_BUY':
                    profit = bid - open_price
                    if profit >= 3 and current_sl < open_price + 1:
                        await connection.modify_position(position_id=p['id'], stop_loss=open_price + 1, take_profit=p['takeProfit'])
                        send_telegram(f"🛡️ XAUUSD BUY posunutý do BE (+1)!")
                elif p_type == 'POSITION_TYPE_SELL':
                    profit = open_price - ask
                    if profit >= 3 and (current_sl > open_price - 1 or current_sl == 0):
                        await connection.modify_position(position_id=p['id'], stop_loss=open_price - 1, take_profit=p['takeProfit'])
                        send_telegram(f"🛡️ XAUUSD SELL posunutý do BE (-1)!")

            # Vstup pre BTCUSD ak žiadny nie je
            if len(btc_positions) == 0:
                symbol_price = await connection.get_symbol_price('BTCUSD')
                ask = symbol_price['ask']
                sl = ask - 400
                tp = ask + 800
                await connection.create_market_buy_order(symbol='BTCUSD', volume=LOT_SIZE, stop_loss=sl, take_profit=tp, comment="riobot-btc")
                send_telegram(f"🟢 Riobot otvoril BUY na BTCUSD (SL: {sl}, TP: {tp})!")

            # Vstup pre XAUUSD ak žiadny nie je
            if len(gold_positions) == 0:
                symbol_price = await connection.get_symbol_price('XAUUSD')
                ask = symbol_price['ask']
                sl = ask - 12
                tp = ask + 10
                await connection.create_market_buy_order(symbol='XAUUSD', volume=LOT_SIZE, stop_loss=sl, take_profit=tp, comment="riobot-gold")
                send_telegram(f"🟢 Riobot otvoril BUY na XAUUSD (SL: {sl}, TP: {tp})!")

            await asyncio.sleep(15)
            
        except Exception as e:
            print(f"Chyba v cykle: {e}")
            send_telegram(f"⚠️ Chyba bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_bot())
