import os
import time
import requests
import asyncio
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAUUSD"
LOT_SIZE = 0.01

MIN_MOVE = 4.0
TP_DISTANCE = 10.0
SL_DISTANCE = 10.0
BE_TRIGGER = 4.0
BE_LOCK = 2.0

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

    print(f"Riobot štartuje: TP {TP_DISTANCE}, SL {SL_DISTANCE}, BE pri +{BE_TRIGGER} -> na +{BE_LOCK} -> aktívny...")

    price_history = []

    while True:
        try:
            price = await terminal_state.get_symbol_price(SYMBOL)
            bid = price["bid"]
            ask = price["ask"]
            
            print(f"{SYMBOL} Cena: {bid}")

            price_history.append(bid)
            if len(price_history) > 30:
                price_history.pop(0)

            positions = await terminal_state.get_positions()

            for pos in positions:
                if pos["symbol"] == SYMBOL:
                    open_price = pos["openPrice"]
                    pos_type = pos["type"]
                    current_sl = pos.get("stopLoss", 0)
                    
                    if pos_type == "POSITION_TYPE_BUY":
                        profit_points = bid - open_price
                        if profit_points >= BE_TRIGGER and current_sl < (open_price + BE_LOCK):
                            new_sl = open_price + BE_LOCK
                            print(f"BE BUY: SL -> {new_sl}")
                            await connection.modify_position(pos["id"], stopLoss=new_sl, takeProfit=pos.get("takeProfit"))
                    
                    elif pos_type == "POSITION_TYPE_SELL":
                        profit_points = open_price - ask
                        if profit_points >= BE_TRIGGER and (current_sl > (open_price - BE_LOCK) or current_sl == 0):
                            new_sl = open_price - BE_LOCK
                            print(f"BE SELL: SL -> {new_sl}")
                            await connection.modify_position(pos["id"], stopLoss=new_sl, takeProfit=pos.get("takeProfit"))

            if len(positions) == 0 and len(price_history) >= 10:
                old_price = price_history[0]
                diff = bid - old_price

                if diff >= MIN_MOVE:
                    print(f"BUY pohyb: {diff}")
                    result = await connection.create_market_buy_order(SYMBOL, LOT_SIZE, ask, ask - SL_DISTANCE, ask + TP_DISTANCE)
                    if result.get("stringCode") == "TRADE_RETCODE_DONE":
                        send_telegram_message(f"🟢 *XAUUSD BUY*\nEntry: `{ask}`\nTP: `{ask + TP_DISTANCE}`\nSL: `{ask - SL_DISTANCE}`")

                elif diff <= -MIN_MOVE:
                    print(f"SELL pohyb: {diff}")
                    result = await connection.create_market_sell_order(SYMBOL, LOT_SIZE, bid, bid + SL_DISTANCE, bid - TP_DISTANCE)
                    if result.get("stringCode`") == "TRADE_RETCODE_DONE":
                        send_telegram_message(f"🔴 *XAUUSD SELL*\nEntry: `{bid}`\nTP: `{bid - TP_DISTANCE}`\nSL: `{bid + SL_DISTANCE}`")

        except Exception as e:
            print(f"Chyba: {e}")

        await asyncio.sleep(20)

if __name__ == "__main__":
    asyncio.run(main())
