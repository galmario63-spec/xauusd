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

LOT_SIZE = 0.01          
SL_POINTS = 15.0         
TP_POINTS = 60.0         
BE_TRIGGER = 10.0        

previous_positions = {}
last_price = None
price_momentum = 0

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}).encode('utf-8')
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("Telegram error:", e)

def is_allowed_trading_time():
    now = datetime.now(datetime.UTC) if hasattr(datetime, 'UTC') else datetime.utcnow()
    hour = now.hour
    if 8 <= hour < 20:
        return True
    return False

async def manage_positions(connection):
    global previous_positions
    try:
        positions = await connection.get_positions()
        current_pos_ids = {p['id'] for p in positions if p['symbol'] == SYMBOL}
        
        for pos_id, pos_info in list(previous_positions.items()):
            if pos_id not in current_pos_ids:
                msg = f"🔴 XAUUSD Obchod uzavrety\nSmer: {pos_info['type']}\nCena: {pos_info['openPrice']}"
                send_telegram_message(msg)
                del previous_positions[pos_id]

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
                    msg = f"⚡ XAUUSD Novy obchod\nSmer: {type_pos}\nCena: {open_price}"
                    send_telegram_message(msg)

                if profit >= BE_TRIGGER:
                    if type_pos == 'POSITION_TYPE_BUY' and current_sl < open_price:
                        await connection.modify_position(position_id=pos_id, stop_loss=open_price, take_profit=pos.get('takeProfit'))
                        send_telegram_message("🛡️ Break-Even aktivovany pre BUY")
                        
                    elif type_pos == 'POSITION_TYPE_SELL' and (current_sl > open_price or current_sl == 0):
                        await connection.modify_position(position_id=pos_id, stop_loss=open_price, take_profit=pos.get('takeProfit'))
                        send_telegram_message("🛡️ Break-Even aktivovany pre SELL")
                        
    except Exception as e:
        print("Error managing positions:", e)

async def check_strategy_and_trade(connection):
    global last_price, price_momentum
    try:
        if not is_allowed_trading_time():
            return

        positions = await connection.get_positions()
        if any(p['symbol'] == SYMBOL for p in positions):
            return

        price = await connection.get_symbol_price(SYMBOL)
        if not price:
            return
            
        bid = price.get('bid')
        ask = price.get('ask')
        if not bid or not ask:
            return

        current_mid = (bid + ask) / 2

        if last_price is not None:
            diff = current_mid - last_price
            if diff > 0.10:
                price_momentum += 1
            elif diff < -0.10:
                price_momentum -= 1
            else:
                price_momentum = 0

        last_price = current_mid

        if price_momentum >= 3:
            price_momentum = 0
            sl = round(ask - 1.50, 2)
            tp = round(ask + 6.00, 2)
            print("Otvaram BUY poziciu")
            await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=sl, take_profit=tp)

        elif price_momentum <= -3:
            price_momentum = 0
            sl = round(bid + 1.50, 2)
            tp = round(bid - 6.00, 2)
            print("Otvaram SELL poziciu")
            await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=sl, take_profit=tp)

    except Exception as e:
        print("Error in strategy:", e)

async def main():
    print("Cakám 5 sekund...")
    await asyncio.sleep(5)
    
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    connection = account.get_rpc_connection()
    
    try:
        if connection.connected:
            await connection.close()
    except:
        pass

    print("Pripajam sa k MetaApi...")
    await connection.connect()
    await connection.wait_synchronized()
    print("Bot bezi stabilne.")
    
    while True:
        try:
            await manage_positions(connection)
            await check_strategy_and_trade(connection)
        except Exception as loop_err:
            print("Loop error:", loop_err)
            
        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
