import time
import requests
import asyncio
import pandas as pd
from metaapi_cloud_sdk import MetaApi
import os

TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAUUSD"
LOT_SIZE = 0.01

MIN_MOVE = 4.0
TP_DISTANCE = 7.0
SL_DISTANCE = 10.0
BE_TRIGGER = 3.0
BE_LOCK = 1.5

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram chyba: {e}")

async def main():
    if not TOKEN or not ACCOUNT_ID:
        print("Chýbajú MetaApi premenné!")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metainfo_account_api.get_account_id(ACCOUNT_ID)
    
    if account.state != "DEPLOYED":
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    
    terminal_state = account.get_terminal_state()
    await terminal_state.wait_synchronized()

    historical_data = account.get_historical_data_provider()

    print("Riobot beží s EMA, MACD, Stochastic filtrom a Break-Even...")
    send_telegram_message("🤖 Riobot úspešne naštartoval s plnou stratégiou (EMA, MACD, Stochastic)! Bežíme.")

    price_history = []

    while True:
        try:
            price = await terminal_state.get_symbol_price(SYMBOL)
            bid = price["bid"]
            ask = price["ask"]

            price_history.append(bid)
            if len(price_history) > 30:
                price_history.pop(0)

            positions = await terminal_state.get_positions()

            # 1. Správa Break-Even
            for pos in positions:
                if pos["symbol"] == SYMBOL:
                    open_price = float(pos["openPrice"])
                    pos_type = pos["type"]
                    current_sl = float(pos.get("stopLoss", 0))
                    
                    if pos_type == "POSITION_TYPE_BUY":
                        profit_points = bid - open_price
                        target_sl = round(open_price + BE_LOCK, 2)
                        if profit_points >= BE_TRIGGER and (current_sl < target_sl):
                            await connection.modify_position(pos["id"], stopLoss=target_sl, takeProfit=float(pos.get("takeProfit", 0)))
                            send_telegram_message(f"🔒 Break-Even posunutý pre BUY na SL: {target_sl}")
                    
                    elif pos_type == "POSITION_TYPE_SELL":
                        profit_points = open_price - ask
                        target_sl = round(open_price - BE_LOCK, 2)
                        if profit_points >= BE_TRIGGER and (current_sl > target_sl or current_sl == 0):
                            await connection.modify_position(pos["id"], stopLoss=target_sl, takeProfit=float(pos.get("takeProfit", 0)))
                            send_telegram_message(f"🔒 Break-Even posunutý pre SELL na SL: {target_sl}")

            # 2. Obchodovanie s EMA, MACD a Stochastic filtrom
            if len(positions) == 0 and len(price_history) >= 10:
                candles = await historical_data.get_candles(SYMBOL, "1m", 100)
                if len(candles) > 60:
                    df = pd.DataFrame(candles)
                    
                    # EMA 20 & 50
                    df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
                    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
                    
                    # MACD (12, 26, 9)
                    exp1 = df['close'].ewm(span=12, adjust=False).mean()
                    exp2 = df['close'].ewm(span=26, adjust=False).mean()
                    df['macd'] = exp1 - exp2
                    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
                    
                    # Stochastic (14, 3)
                    low_14 = df['low'].rolling(window=14).min()
                    high_14 = df['high'].rolling(window=14).max()
                    df['stoch_k'] = 100 * (df['close'] - low_14) / (high_14 - low_14)
                    df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()
                    
                    last_close = df['close'].iloc[-1]
                    ema_20_val = df['ema_20'].iloc[-1]
                    ema_50_val = df['ema_50'].iloc[-1]
                    macd_val = df['macd'].iloc[-1]
                    signal_val = df['macd_signal'].iloc[-1]
                    stoch_k = df['stoch_k'].iloc[-1]
                    stoch_d = df['stoch_d'].iloc[-1]

                    old_price = price_history[0]
                    diff = bid - old_price

                    buy_filter = (last_close > ema_20_val) and (ema_20_val > ema_50_val) and (macd_val > signal_val) and (stoch_k > stoch_d)
                    sell_filter = (last_close < ema_20_val) and (ema_20_val < ema_50_val) and (macd_val < signal_val) and (stoch_k < stoch_d)

                    if diff >= MIN_MOVE and buy_filter:
                        result = await connection.create_market_buy_order(SYMBOL, LOT_SIZE, ask, ask - SL_DISTANCE, ask + TP_DISTANCE)
                        if result.get("stringCode") == "TRADE_RETCODE_DONE":
                            send_telegram_message(f"🟢 XAUUSD BUY (EMA+MACD+Stoch)\nEntry: {ask}\nTP: {ask + TP_DISTANCE}\nSL: {ask - SL_DISTANCE}")

                    elif diff <= -MIN_MOVE and sell_filter:
                        result = await connection.create_market_sell_order(SYMBOL, LOT_SIZE, bid, bid + SL_DISTANCE, bid - TP_DISTANCE)
                        if result.get("stringCode") == "TRADE_RETCODE_DONE":
                            send_telegram_message(f"🔴 XAUUSD SELL (EMA+MACD+Stoch)\nEntry: {bid}\nTP: {bid - TP_DISTANCE}\nSL: {bid + SL_DISTANCE}")

        except Exception as e:
            print(f"Chyba: {e}")

        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
