import os
import time
import requests
import asyncio
from metaapi_cloud_sdk import MetaApi

# Načítanie premenných z Railway
TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "XAUUSD"
LOT_SIZE = 0.01

# Obchodná logika
MIN_MOVE = 4.0        # Minimálny pohyb pre vstup
TP_DISTANCE = 10.0    # Take Profit na 10 bodoch
SL_DISTANCE = 10.0    # Stop Loss na 10 bodoch

# Break-Even nastavenie
BE_TRIGGER = 4.0      # Pri akom zisku sa aktivuje BE
BE_LOCK = 2.0         # Na akú úroveň plusu sa posunie SL

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Chyba pri odosielaní Telegram správy: {e}")

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

            # Kontrola Break-Even pre existujúce pozície
            for pos in positions:
                if pos["symbol"] == SYMBOL:
                    open_price = pos["openPrice"]
                    pos_type = pos["type"]
                    current_sl = pos.get("stopLoss", 0)
                    
                    if pos_type == "POSITION_TYPE_BUY":
                        profit_points = bid - open_price
                        # Ak dosiahol zisk +4 a SL ešte nie je posunutý na +2
                        if profit_points >= BE_TRIGGER and current_sl < (open_price + BE_LOCK):
                            new_sl = open_price + BE_LOCK
                            print(f"Aktivujem Break-Even pre BUY! Posúvam SL na {new_sl}")
                            await connection.modify_position(pos["id"], stopLoss=new_sl, takeProfit=pos.get("takeProfit"))
                    
                    elif pos_type == "POSITION_TYPE_SELL":
                        profit_points = open_price - ask
                        # Ak dosiahol zisk +4 a SL ešte nie je posunutý na +2 do plusu
                        if profit_points >= BE_TRIGGER and (current_sl > (open_price - BE_LOCK) or current_sl == 0):
                            new_sl = open_price - BE_LOCK
                            print(f"Aktivujem Break-Even pre SELL! Posúvam SL na {new_sl}")
                            await connection.modify_position(pos["id"], stopLoss=new_sl, takeProfit=pos.get("takeProfit"))

            # Podmienka: Otvoriť obchod len ak nie je žiadna pozícia
            if len(positions) == 0 and len(price_history) >= 10:
                old_price = price_history[0]
                diff = bid - old_price

                # BUY logika
                if diff >= MIN_MOVE:
                    print(f"Detekovaný silný BUY pohyb: {diff}")
                    result = await connection.create_market_buy_order(SYMBOL, LOT_SIZE, ask, ask - SL_DISTANCE, ask + TP_DISTANCE)
                    
                    if result.get("stringCode") == "TRADE_RETCODE_DONE":
                        msg = f"🟢 *XAUUSD BUY*\n\nEntry: `{ask}`\nTP: `{ask + TP_DISTANCE}`\nSL: `{ask - SL_DISTANCE}`"
                        send_telegram_message(msg)

                # SELL logika
                elif diff <= -MIN_MOVE:
                    print(f"Detekovaný silný SELL pohyb: {diff}")
                    result = await connection.create_market_sell_order(SYMBOL, LOT_SIZE, bid, bid + SL_DISTANCE, bid - TP_DISTANCE)
                    
                    if result.get("stringCode") == "TRADE_RETCODE_DONE":
                        msg = f"🔴 *XAUUSD SELL*\n\nEntry: `{bid}`\nTP: `{bid - TP_DISTANCE}`\nSL: `{bid + SL_DISTANCE}`"
                        send_telegram_message(msg)

        except Exception as e:
            print(f"Chyba v cykle: {e}")

        await asyncio.sleep(20)

if __name__ == "__main__":
    asyncio.run(main())
