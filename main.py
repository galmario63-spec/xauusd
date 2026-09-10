import os
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
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Chyba pri odosielaní Telegram správy: {e}")

async def main():
    if not TOKEN or not ACCOUNT_ID:
        print("Chýbajú MetaApi premenné!")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != "DEPLOYED":
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()

    terminal_state = account.get_terminal_state()
    await terminal_state.wait_synchronized()

    print("Riobot beží...")
    send_telegram_message("🤖 Riobot štartuje a pripája sa k XAUUSD.")

    price_history = []

    while True:
        try:
            price = await terminal_state.get_symbol_price(SYMBOL)
            bid = price.get('bid')
            ask = price.get('ask')

            if not bid or not ask:
                await asyncio.sleep(1)
                continue

            current_price = (bid + ask) / 2
            price_history.append(current_price)
            if len(price_history) > 100:
                price_history.pop(0)

            if len(price_history) >= 2:
                prev_price = price_history[-2]
                move = current_price - prev_price

                if abs(move) >= MIN_MOVE:
                    action_type = "ORDER_TYPE_BUY" if move > 0 else "ORDER_TYPE_SELL"
                    open_price = ask if action_type == "ORDER_TYPE_BUY" else bid
                    tp = open_price + TP_DISTANCE if action_type == "ORDER_TYPE_BUY" else open_price - TP_DISTANCE
                    sl = open_price - SL_DISTANCE if action_type == "ORDER_TYPE_BUY" else open_price + SL_DISTANCE

                    print(f"Signál detekovaný! Smer: {action_type}, Cena: {open_price}")

                    result = await connection.create_market_order(
                        symbol=SYMBOL,
                        action_type=action_type,
                        volume=LOT_SIZE,
                        stop_loss=sl,
                        take_profit=tp
                    )
                    
                    if result.get('stringCode') == 'TRADE_RETCODE_DONE':
                        ticket = result.get('order')
                        msg = f"✅ Pozícia otvorená ({SYMBOL})\nTyp: {action_type}\nCena: {open_price}\nTP: {tp}\nSL: {sl}"
                        send_telegram_message(msg)
                    else:
                        print(f"Chyba pri otváraní pozície: {result}")

            positions = await terminal_state.get_positions()
            for pos in positions:
                if pos['symbol'] == SYMBOL:
                    ticket = pos['id']
                    open_price = pos['openPrice']
                    current_profit = pos['profit']
                    pos_type = pos['type']

                    if pos_type == 'POSITION_TYPE_BUY':
                        if current_profit >= BE_TRIGGER * 10: 
                            new_sl = open_price + BE_LOCK
                            if pos.get('stopLoss', 0) < new_sl:
                                await connection.modify_position(ticket, stop_loss=new_sl, take_profit=pos.get('takeProfit'))
                                send_telegram_message(f"🔒 Posunutý BE na Buy pozícii (#{ticket})")

                    elif pos_type == 'POSITION_TYPE_SELL':
                        if current_profit >= BE_TRIGGER * 10:
                            new_sl = open_price - BE_LOCK
                            if pos.get('stopLoss', 99999) > new_sl:
                                await connection.modify_position(ticket, stop_loss=new_sl, take_profit=pos.get('takeProfit'))
                                send_telegram_message(f"🔒 Posunutý BE na Sell pozícii (#{ticket})")

            await asyncio.sleep(2)

        except Exception as e:
            print(f"Chyba v hlavnej slučke: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
