import os
import time
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
import requests
import pandas as pd
import numpy as np

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

SYMBOL = "BTCUSD"
LOT_SIZE = 0.1
TIMEFRAME = "5m"

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
    
    # 1. Inicializácia prebehne IBA RAZ pri štarte aplikácie
    send_telegram("🚀 Riobot sa inicializuje...")
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
        
    await account.wait_connected()
    connection = account.get_rpc_connection()
    
    await connection.connect()
    await connection.wait_synchronized()
        
    send_telegram("🚀 Riobot úspešne pripojený a pripravený pre BUY aj SELL (bez Stochastiku)!")
    
    # 2. Hlavná nekonečná slučka
    while True:
        try:
            # Kontrola otvorených pozícií a správa Break-Even (BE)
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]
            
            # Zisťujeme aktuálne ceny cez candles/ohlc z MetaApi
            # (Pre zjednodušenie a rýchlosť hlavnej slučky)
            
            for p in btc_positions:
                open_price = p['openPrice']
                p_type = p['type'] # POSITION_TYPE_BUY alebo POSITION_TYPE_SELL
                current_sl = p.get('stopLoss', 0)
                
                # Získame aktuálnu cenu symbolu
                symbol_price = await connection.get_symbol_price(SYMBOL)
                current_bid = symbol_price['bid']
                current_ask = symbol_price['ask']
                
                if p_type == 'POSITION_TYPE_BUY':
                    profit_points = current_bid - open_price
                    # Ak je zisk > 300 bodov a SL ešte nie je na BE
                    if profit_points >= 300 and (current_sl < open_price or current_sl == 0):
                        await connection.modify_position(
                            position_id=p['id'],
                            stop_loss=open_price,
                            take_profit=p['takeProfit']
                        )
                        send_telegram(f"🛡️ Riobot posunul BUY pozíciu do Break-Even (BE) na {open_price}!")
                        
                elif p_type == 'POSITION_TYPE_SELL':
                    profit_points = open_price - current_ask
                    # Ak je zisk > 300 bodov a SL ešte nie je na BE
                    if profit_points >= 300 and (current_sl > open_price or current_sl == 0):
                        await connection.modify_position(
                            position_id=p['id'],
                            stop_loss=open_price,
                            take_profit=p['takeProfit']
                        )
                        send_telegram(f"🛡️ Riobot posunul SELL pozíciu do Break-Even (BE) na {open_price}!")

            # Ak nemá žiadnu otvorenú pozíciu pre tento symbol, hľadáme nový vstup (BUY aj SELL)
            if len(btc_positions) == 0:
                symbol_price = await connection.get_symbol_price(SYMBOL)
                bid = symbol_price['bid']
                ask = symbol_price['ask']
                
                # Príklad logiky: Ak sa podmienky splnia, bot otvorí obchod
                # (Môžeš si tu upraviť vlastné spúšťače pre BUY / SELL na základe EMA / MACD)
                
                # Ukážka pre BUY (odkomentuj alebo prispôsob podľa potreby):
                # sl_buy = ask - 400
                # tp_buy = ask + 800
                # await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl_buy, tp_buy, comment="riobot-buy")
                # send_telegram(f"🟢 Riobot otvoril BUY na {ask}!")

                # Ukážka pre SELL (odkomentuj alebo prispôsob podľa potreby):
                # sl_sell = bid + 400
                # tp_sell = bid - 800
                # await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl_sell, tp_sell, comment="riobot-sell")
                # send_telegram(f"🔴 Riobot otvoril SELL na {bid}!")

            await asyncio.sleep(15)
            
        except Exception as e:
            print(f"Chyba v cykle: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_bot())
