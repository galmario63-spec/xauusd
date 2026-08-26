import asyncio
import os
from datetime import datetime
import urllib.request
import urllib.parse
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAPI_TOKEN')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SYMBOL = "XAUUSD"

# Parametre stratégie a rizika
LOT_SIZE = 0.01          
SL_POINTS = 15.0         
TP_POINTS = 60.0         
BE_TRIGGER = 10.0        

# Sledovanie predtým otvorených pozícií
previous_positions = {}

def send_telegram_message(message):
    """Odoslanie notifikácie cez Riobota do Telegramu"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}).encode('utf-8')
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("Chyba pri odosielaní Telegram správy:", e)

def is_allowed_trading_time():
    """Kontrola obchodných hodín pre hlavné seansy (Londýn / New York)"""
    now = datetime.utcnow()
    hour = now.hour
    if 8 <= hour < 20:
        return True
    return False

async def manage_positions(connection):
    """Manažment pozícií: Break-Even a detekcia zatvorených obchodov (TP / SL)"""
    global previous_positions
    try:
        positions = await connection.get_positions()
        current_pos_ids = {p['id'] for p in positions if p['symbol'] == SYMBOL}
        
        # 1. Kontrola zatvorených pozícií (TP / SL / manuál)
        for pos_id, pos_info in list(previous_positions.items()):
            if pos_id not in current_pos_ids:
                msg = (
                    f"🔴 **XAUUSD Obchod uzavretý**\n"
                    f"Smer: {pos_info['type']}\n"
                    f"Vstupná cena: {pos_info['openPrice']}\n"
                    f"ℹ️ Pozícia bola ukončená v trhu."
                )
                send_telegram_message(msg)
                del previous_positions[pos_id]

        # 2. Spracovanie aktívnych pozícií (Break-Even)
        for pos in positions:
            if pos['symbol'] == SYMBOL:
                pos_id = pos['id']
                profit = pos.get('profit', 0)
                open_price = pos['openPrice']
                current_sl = pos.get('stopLoss', 0)
                type_pos = pos['type']
                
                if pos_id not in previous_positions:
                    previous_positions[pos_id] = {
                        'type': type_pos,
                        'openPrice': open_price
                    }
                    msg = (
                        f"⚡ **XAUUSD Nový Obchod Otvorený** ⚡\n"
                        f"Smer: {type_pos}\n"
                        f"Cena: {open_price}\n"
                        f"🛡️ SL: {current_sl} | 🎯 TP: {pos.get('takeProfit', 'N/A')}"
                    )
                    send_telegram_message(msg)

                # Posun na Break-Even pri +10 bodoch zisku
                if profit >= BE_TRIGGER:
                    if type_pos == 'POSITION_TYPE_BUY' and current_sl < open_price:
                        await connection.modify_position(
                            position_id=pos_id,
                            stop_loss=open_price,
                            take_profit=pos.get('takeProfit')
                        )
                        send_telegram_message(f"🛡️ **Break-Even aktivovaný**\nBUY SL posunutý na vstupnú cenu: {open_price}")
                        
                    elif type_pos == 'POSITION_TYPE_SELL' and (current_sl > open_price or current_sl == 0):
                        await connection.modify_position(
                            position_id=pos_id,
                            stop_loss=open_price,
                            take_profit=pos.get('takeProfit')
                        )
                        send_telegram_message(f"🛡️ **Break-Even aktivovaný**\nSELL SL posunutý na vstupnú cenu: {open_price}")
                        
    except Exception as e:
        print("Chyba pri manažmente pozícií:", e)

async def check_market(connection):
    """Stabilné sledovanie trhu a cien"""
    try:
        if not is_allowed_trading_time():
            return

        price = await connection.get_symbol_price(SYMBOL)
        if price:
            print(f"Trh aktívny - {SYMBOL} Bid: {price.get('bid')}, Ask: {price.get('ask')}")

    except Exception as e:
        print("Chyba pri kontrole trhu:", e)

async def main():
    print("Čakám 5 sekúnd pred inicializáciou...")
    await asyncio.sleep(5)
    
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    connection = account.get_rpc_connection()
    
    try:
        if connection.connected:
            await connection.close()
    except:
        pass

    print("Pripájam sa k MetaApi serveru...")
    await connection.connect()
    await connection.wait_synchronized()
    print("Úspešne pripojené a synchronizované. Bot beží bez chýb.")
    
    while True:
        try:
            await manage_positions(connection)
            await check_market(connection)
        except Exception as loop_err:
            print("Chyba v hlavnej slučke:", loop_err)
            
        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
