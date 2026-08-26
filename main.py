import asyncio
import os
from datetime import datetime, timedelta
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

# Sledovanie predtým otvorených pozícií a poslednej spracovanej sviečky
previous_positions = {}
last_processed_candle_time = None

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

async def check_strategy_and_trade(account, connection):
    """Obchodná logika: Engulfing formácia na M5 cez Client API sviečky"""
    global last_processed_candle_time
    try:
        if not is_allowed_trading_time():
            return

        # Skontrolujeme, či už nie je otvorený nejaký obchod (chceme iba 1 pozíciu naraz)
        positions = await connection.get_positions()
        if any(p['symbol'] == SYMBOL for p in positions):
            return

        # Stiahneme historické sviečky cez stabilné MetaApi Client API
        client_api = account.get_client_api()
        now = datetime.utcnow()
        start_time = now - timedelta(hours=2)
        
        candles = await client_api.get_historical_candles(ACCOUNT_ID, SYMBOL, '5m', start_time)
        
        if not candles or len(candles) < 3:
            return

        # candles[-2] je už uzavretá sviečka, candles[-3] je predchádzajúca
        prev_candle = candles[-3]
        closed_candle = candles[-2]
        candle_time = closed_candle['time']

        # Ak sme túto sviečku už vyhodnocovali, nerobíme to znovu
        if last_processed_candle_time == candle_time:
            return

        # Zistíme ceny pre výpočet SL a TP
        price = await connection.get_symbol_price(SYMBOL)
        if not price:
            return
        bid = price.get('bid')
        ask = price.get('ask')
        if not bid or not ask:
            return

        # Definícia farieb sviečok (Open vs Close)
        prev_is_bearish = prev_candle['close'] < prev_candle['open']
        prev_is_bullish = prev_candle['close'] > prev_candle['open']
        closed_is_bullish = closed_candle['close'] > closed_candle['open']
        closed_is_bearish = closed_candle['close'] < closed_candle['open']

        # 1. BÝČÍ ENGULFING (Signál na BUY)
        is_bullish_engulfing = (
            prev_is_bearish and 
            closed_is_bullish and 
            closed_candle['open'] <= prev_candle['close'] and 
            closed_candle['close'] >= prev_candle['open']
        )

        # 2. MEDVEDÍ ENGULFING (Signál na SELL)
        is_bearish_engulfing = (
            prev_is_bullish and 
            closed_is_bearish and 
            closed_candle['open'] >= prev_candle['close'] and 
            closed_candle['close'] <= prev_candle['open']
        )

        if is_bullish_engulfing:
            last_processed_candle_time = candle_time
            sl = round(ask - 1.50, 2)
            tp = round(ask + 6.00, 2)
            
            print(f"Zistený Býčí Engulfing! Otváram BUY pozíciu na {SYMBOL}")
            await connection.create_market_buy_order(
                symbol=SYMBOL,
                volume=LOT_SIZE,
                stop_loss=sl,
                take_profit=tp
            )

        elif is_bearish_engulfing:
            last_processed_candle_time = candle_time
            sl = round(bid + 1.50, 2)
            tp = round(bid - 6.00, 2)
            
            print(f"Zistený Medvedí Engulfing! Otváram SELL pozíciu na {SYMBOL}")
            await connection.create_market_sell_order(
                symbol=SYMBOL,
                volume=LOT_SIZE,
                stop_loss=sl,
                take_profit=tp
            )

    except Exception as e:
        print("Chyba v obchodnej logike:", e)

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
    print("Úspešne pripojené a synchronizované. Engulfing stratégia a Riobot sú pripravené.")
    
    while True:
        try:
            await manage_positions(connection)
            await check_strategy_and_trade(account, connection)
        except Exception as loop_err:
            print("Chyba v hlavnej slučke:", loop_err)
            
        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
