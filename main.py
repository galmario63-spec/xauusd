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
    return "Riobot Parabolic SAR Target 3EUR Active"

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
LOT_SIZE = 0.30         # Zvýšené na 0.30 pre 3 € cieľ
TP_POINTS = 600.0       # 600 bodov = cca 3 € zisk pri 0.30 lote
BE_TRIGGER = 250.0      # Posun BE pri 250 bodoch zisku
BE_LOCK = 100.0         # Zámok v zisku na BE
SL_POINTS = 1500.0      # Pevný SL

SAR_STEP = 0.80
SAR_MAX = 0.40

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

def calculate_parabolic_sar(candles):
    if not candles or len(candles) < 5:
        return None
    highs = [c['high'] for c in candles]
    lows = [c['low'] for c in candles]
    closes = [c['close'] for c in candles]
    
    if closes[-2] > highs[-3]:
        return "BUY"
    elif closes[-2] < lows[-3]:
        return "SELL"
    return None

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
        send_telegram("🚀 Riobot SAR (Cieľ 3€ / Lot 0.30) pripravený.")
        startup_message_sent = True

    while True:
        try:
            positions = await connection.get_positions()
            btc_positions = [p for p in positions if p['symbol'] == SYMBOL]

            price_info = await connection.get_symbol_price(SYMBOL)
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
                            send_telegram("🔒 BE aktívne (BUY) - 3€ cieľ")
                            
                elif pos['type'] == 'POSITION_TYPE_SELL' and ask:
                    if (open_price - ask) >= BE_TRIGGER:
                        target_sl = open_price - BE_LOCK
                        if current_sl > target_sl or current_sl == 0:
                            await connection.modify_position(
                                positionId=pos['id'], stop_loss=target_sl, take_profit=pos.get('takeProfit', open_price - TP_POINTS)
                            )
                            send_telegram("🔒 BE aktívne (SELL) - 3€ cieľ")

            # Vstupy na základe SAR na M1
            if len(btc_positions) == 0 and ask and bid:
                try:
                    candles = await connection.get_historical_candles(SYMBOL, "1m", None, 10)
                except Exception:
                    candles = None
                
                if candles and len(candles) >= 5:
                    curr_c = candles[-2]
                    c_time = curr_c.get('time')
                    
                    if last_processed_candle != c_time:
                        signal = calculate_parabolic_sar(candles)
                        
                        if signal == "BUY":
                            sl = ask - SL_POINTS
                            tp = ask + TP_POINTS
                            await connection.create_market_buy_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_processed_candle = c_time
                            send_telegram("🟢 M1 SAR BUY (Lot 0.30 -> 3€ TP) otvorený.")
                            await asyncio.sleep(20)
                            
                        elif signal == "SELL":
                            sl = bid + SL_POINTS
                            tp = bid - TP_POINTS
                            await connection.create_market_sell_order(SYMBOL, LOT_SIZE, stop_loss=sl, take_profit=tp)
                            last_processed_candle = c_time
                            send_telegram("🔴 M1 SAR SELL (Lot 0.30 -> 3€ TP) otvorený.")
                            await asyncio.sleep(20)

            await asyncio.sleep(5)

        except Exception as inner_e:
            print(f"Chyba: {inner_e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
