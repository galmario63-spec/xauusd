import os
import time
import asyncio
from flask import Flask
from threading import Thread
from metaapi_cloud_sdk import MetaApi
requests_mod = __import__('requests')

app = Flask('')

@app.route('/')
def home():
    return "Riobot Direct SAR Active"

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

LOT_SIZE = 0.30         
TP_POINTS = 600.0       
BE_TRIGGER = 250.0      
BE_LOCK = 100.0         
SL_POINTS = 1500.0      

last_position_count = 0
startup_message_sent = False

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests_mod.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
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
    
    symbol = "BTCUSD"
    try:
        specifications = await connection.get_symbol_specifications()
        for spec in specifications:
            s_name = spec.get('symbol', '')
            if 'BTC' in s_name.upper():
                symbol = s_name
                break
    except Exception:
        pass

    if not startup_message_sent:
        send_telegram(f"🚀 Riobot Direct SAR beží naostro pre {symbol}")
        startup_message_sent = True

    while True:
        try:
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == symbol]
            price_info = await connection.get_symbol_price(symbol)
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

            # Okamžitá kontrola indikátora priamo z MetaTrader servera cez technické hodnoty sviečok
            if len(btc_positions) == 0 and ask and bid:
                candles = await connection.get_historical_candles(symbol, "1m", None, 5)
                if candles and len(candles) >= 3:
                    c1 = candles[-2] # Uzavretá sviečka
                    c0 = candles[-1] # Aktuálna rozrobene sviečka
                    
                    # Sledujeme smer pohybu sviečky (momentum preklopenia)
                    is_bullish = c1['close'] > c1['open'] and c0['close'] > c0['open']
                    is_bearish = c1['close'] < c1['open'] and c0['close'] < c0['open']
                    
                    if is_bullish:
                        sl = ask - SL_POINTS
                        tp = ask + TP_POINTS
                        await connection.create_market_buy_order(symbol, LOT_SIZE, stop_loss=sl, take_profit=tp)
                        send_telegram(f"🟢 {symbol} BUY (0.30 Lot) otvorený okamžite.")
                        await asyncio.sleep(30)
                    elif is_bearish:
                        sl = bid + SL_POINTS
                        tp = bid - TP_POINTS
                        await connection.create_market_sell_order(symbol, LOT_SIZE, stop_loss=sl, take_profit=tp)
                        send_telegram(f"🔴 {symbol} SELL (0.30 Lot) otvorený okamžite.")
                        await asyncio.sleep(30)

            await asyncio.sleep(1)
        except Exception as inner_e:
            print(f"Chyba: {inner_e}")
            await asyncio.sleep(3)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
