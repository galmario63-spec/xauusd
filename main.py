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
        print(f"Chyba: {e}")

async def main():
    if not TOKEN or not ACCOUNT_ID:
        print("Chýbajú premenné!")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != "DEPLOYED":
        await account.deploy()

    print("Pripájam sa k účtu...")
    connection = account.get_rpc_connection()
    await connection.connect()
    
    # Počkáme na synchronizáciu terminálu
    await connection.wait_synchronized()

    print("Riobot beží...")
    send_telegram_message("🤖 Riobot štartuje a je pripojený k XAUUSD.")

    while True:
        try:
            # Získanie cien cez RPC connection
            price = await connection.get_symbol_price(SYMBOL)
            bid = price.get('bid')
            ask = price.get('ask')

            if not bid or not ask:
                await asyncio.sleep(1)
                continue

            current_price = (bid + ask) / 2
            
            # Jednoduchá kontrola pozícií cez RPC
            positions = await connection.get_positions()
            
            await asyncio.sleep(2)

        except Exception as e:
            print(f"Chyba v slučke: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
