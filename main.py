import os
import time
import asyncio
import aiohttp
from fastapi import FastAPI
import uvicorn
from metaapi_cloud_sdk import MetaApi

# --- WEB SERVER PRE RAILWAY ---
app = FastAPI()

@app.get("/")
def health_check():
    return {"status": "running"}

def run_web():
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

# --- HLAVNÝ TRADING BOT ---
TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', 'Tvoj_Telegram_Bot_Token')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'Tvoj_Chat_ID')

FIB_MIN = 0.50
FIB_MAX = 0.618
LOT_SIZE = 0.01

price_history = []

async def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                await response.text()
    except Exception as e:
        print(f"Telegram chyba: {e}")

async def main():
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
        await asyncio.sleep(10)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    print("Riobot stabilne online. SL: 12 | TP1: 6 | TP2: 9 | BE pri zisku 3 -> +1")
    await send_telegram("🤖 Riobot online: SL 12, TP (6/9), BE +1 pri zisku 3.")

    while True:
        try:
            await asyncio.sleep(30)

            price_data = await connection.get_symbol_price(SYMBOL)
            if not price_data: continue

            current_bid = price_data.get('bid')
            current_ask = price_data.get('ask')
            if not current_bid or not current_ask: continue

            price_history.append(current_bid)
            if len(price_history) > 30:
                price_history.pop(0)

            # 1. Break-Even kontrola (zisk 3 -> SL na +1)
            positions = await connection.get_positions()
            for p in positions:
                if p.get('symbol') == SYMBOL:
                    open_price = p.get('openPrice', 0)
                    pos_type = str(p.get('type', ''))
                    current_sl = p.get('stopLoss', 0)
                    
                    if 'BUY' in pos_type:
                        profit = current_bid - open_price
                        if profit >= 3.0 and current_sl < (open_price + 1.0):
                            new_sl = open_price + 1.0
                            await connection.modify_position(p['id'], stop_loss=new_sl, take_profit=p.get('takeProfit'))
                            print(f"BE aktivovaný pre BUY! SL na {new_sl}")
                            await send_telegram(f"🛡️ Break-Even na XAUUSD BUY: SL posunutý na +1$ ({new_sl})")
                            
                    elif 'SELL' in pos_type:
                        profit = open_price - current_ask
                        if profit >= 3.0 and (current_sl > (open_price - 1.0) or current_sl == 0):
                            new_sl = open_price - 1.0
                            await connection.modify_position(p['id'], stop_loss=new_sl, take_profit=p.get('takeProfit'))
                            print(f"BE aktivovaný pre SELL! SL na {new_sl}")
                            await send_telegram(f"🛡️ Break-Even na XAUUSD SELL: SL posunutý na +1$ ({new_sl})")

            # 2. Vstupy na základe Fibonacciho zóny
            has_position = any(p.get('symbol') == SYMBOL for p in positions)
            if not has_position and len(price_history) >= 20:
                max_price = max(price_history)
                min_price = min(price_history)
                diff = max_price - min_price

                if diff > 0:
                    fib_50 = max_price - (diff * FIB_MIN)
                    fib_618 = max_price - (diff * FIB_MAX)

                    # BUY
                    if min_price < current_bid and (min(fib_50, fib_618) <= current_bid <= max(fib_50, fib_618)):
                        sl = current_bid - 12.0
                        tp1 = current_bid + 6.0
                        tp2 = current_bid + 9.0
                        
                        await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp1)
                        await connection.create_market_buy_order(SYMBOL, LOT_SIZE, sl, tp2)
                        
                        msg = f"🔴 XAUUSD BUY – RIO_ENGINE\n\nEntry: {current_bid}\nTP1 (6$): {tp1}\nTP2 (9$): {tp2}\nSL: {sl}"
                        await send_telegram(msg)
                        price_history.clear()

                    # SELL
                    elif max_price > current_bid and (min(fib_50, fib_618) <= current_bid <= max(fib_50, fib_618)):
                        sl = current_bid + 12.0
                        tp1 = current_bid - 6.0
                        tp2 = current_bid - 9.0
                        
                        await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp1)
                        await connection.create_market_sell_order(SYMBOL, LOT_SIZE, sl, tp2)
                        
                        msg = f"🔴 XAUUSD SELL – RIO_ENGINE\n\nEntry: {current_bid}\nTP1 (6$): {tp1}\nTP2 (9$): {tp2}\nSL: {sl}"
                        await send_telegram(msg)
                        price_history.clear()

        except Exception as e:
            print(f"Chyba: {e}")
            await asyncio.sleep(20)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.run(main())
