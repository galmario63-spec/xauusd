import os
os.system("pip install requests metaapi-cloud-sdk pandas")

import time
import requests
import asyncio
import pandas as pd
from metaapi_cloud_sdk import MetaApi

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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
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

    # Získanie historical client pre dáta sviečok (indikátory)
    historical_data = account.get_historical_data_provider()

    print("Riobot beží, kontroluje indikátory a Break-Even...")

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

            # 1. Správa Break-Even pre otvorené pozície
            for pos in positions:
                if pos["symbol"] == SYMBOL:
                    open_price = float(pos["openPrice"])
                    pos_type = pos["type"]
                    current_sl = float(pos.get("stopLoss", 0))
                    
                    if pos_type == "POSITION_TYPE_BUY":
                        profit_points = bid - open_price
                        target_sl = round(open_price + BE_LOCK, 2)
                        if profit_points >= BE_TRIGGER and (current_sl < target_sl):
                            print(f"Opravujem BE BUY: SL -> {target_sl}")
                            await connection.modify_position(pos["id"], stopLoss=target_sl, takeProfit=float(pos.get("takeProfit", 0)))
                    
                    elif pos_type == "POSITION_TYPE_SELL":
                        profit_points = open_price - ask
                        target_sl = round(open_price - BE_LOCK, 2)
                        if profit_points >= BE_TRIGGER and (current_sl > target_sl or current_sl == 0):
                            print(f"Opravujem BE SELL: SL -> {target_sl}")
                            await connection.modify_position(pos["id"], stopLoss=target_sl, takeProfit=float(pos.get("takeProfit", 0)))

            # 2. Vyhodnocovanie nových obchodov s filtrom sviečok / EMA / trendu
            if len(positions) == 0 and len(price_history) >= 10:
                # Načítanie sviečok pre výpočet EMA (napr. 1-minútové sviečky)
                candles = await historical_data.get_candles(SYMBOL, "1m", 60)
                if len(candles) > 50:
                    df = pd.DataFrame(candles)
                    df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
                    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
                    
                    last_close = df['close'].iloc[-1]
                    ema_20_val = df['ema_20'].iloc[-1]
                    ema_50_val = df['ema_50'].iloc[-1]

                    old_price = price_history[0]
                    diff = bid - old_price

                    # Podmienka BUY: Cena rastie + EMA filtre (cena nad EMA a EMA20 nad EMA50)
                    if diff >= MIN_MOVE and (last_close > ema_20_val and ema_20_val > ema_50_val):
                        result = await connection.create_market_buy_order(SYMBOL, LOT_SIZE, ask, ask - SL_DISTANCE, ask + TP_DISTANCE)
                        if result.get("stringCode") == "TRADE_RETCODE_DONE":
                            send_telegram_message(f"🟢 *XAUUSD BUY*\nEntry: `{ask}`\nTP: `{ask + TP_DISTANCE}`\nSL: `{ask - SL_DISTANCE}`")

                    # Podmienka SELL: Cena klesá + EMA filtre (cena pod EMA a EMA20 pod EMA50)
                    elif diff <= -MIN_MOVE and (last_close < ema_20_val and ema_20_val < ema_50_val):
                        result = await connection.create_market_sell_order(SYMBOL, LOT_SIZE, bid, bid + SL_DISTANCE, bid - TP_DISTANCE)
                        if result.get("stringCode") == "TRADE_RETCODE_DONE":
                            send_telegram_message(f"🔴 *XAUUSD SELL*\nEntry: `{bid}`\nTP: `{bid - TP_DISTANCE}`\nSL: `{bid + SL_DISTANCE}`")

        except Exception as e:
            print(f"Chyba: {e}")

        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
