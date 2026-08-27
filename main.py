import asyncio
import logging
import os
import aiohttp
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- NASTAVENIA IBA PRE CENTOVÝ ÚČET ---
CENT_ACCOUNT_ID = "6ec9b96d-841d-4c69-83b2-681f61a4b626"
TOKEN = os.getenv("METAAPI_TOKEN", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = "XAUUSD"
LOT_PER_PART = 0.10
SL_POINTS = 10.0
TP1_POINTS = 20.0
TP2_POINTS = 30.0
BE_TRIGGER_PRICE_DIFF = 2.0  # BE pri zisku 20 centov (2 body)

async def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                pass
    except Exception as e:
        logging.error(f"Telegram error: {e}")

async def check_pa(connection):
    try:
        candles = await connection.get_historical_candles(SYMBOL, "15m", 20)
        if not candles or len(candles) < 5:
            return False
        last = candles[-1]
        prev = candles[-2]
        body = abs(last["close"] - last["open"])
        bullish_engulfing = (last["close"] > last["open"] and prev["close"] < prev["open"] and last["close"] >= prev["open"])
        return bullish_engulfing or body > 2.0
    except:
        return False

async def run_bot():
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(CENT_ACCOUNT_ID)
    if account.state != "DEPLOYED":
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    logging.info("[CENT] Bot úspešne spustený a sleduje trh...")

    while True:
        try:
            positions = await connection.get_positions()
            symbol_pos = [p for p in positions if p["symbol"] == SYMBOL]

            # 1. VSTUP (3 obchody po 0.10 lotu)
            if len(symbol_pos) == 0:
                if await check_pa(connection):
                    price = await connection.get_symbol_price(SYMBOL)
                    ask = price["ask"]
                    
                    msg = "🟢 <b>[CENTOVÝ ÚČET] Nový vstup XAUUSD</b>\n"
                    for i in range(3):
                        sl = ask - SL_POINTS
                        tp = ask + TP1_POINTS if i < 2 else ask + TP2_POINTS
                        label = "TP1 (1:2)" if i < 2 else "TP2 (1:3)"
                        
                        await connection.create_market_buy_order(SYMBOL, LOT_PER_PART, stop_loss=sl, take_profit=tp)
                        msg += f"• Obchod {i+1}: 0.10 lot ({label})\n"
                    
                    await send_telegram(msg)

            # 2. BREAK-EVEN SPRÁVA
            for pos in symbol_pos:
                if pos["type"] == "POSITION_TYPE_BUY":
                    open_p = pos["openPrice"]
                    curr_p = pos["price"]
                    sl = pos.get("stopLoss", 0)
                    
                    if curr_p - open_p >= BE_TRIGGER_PRICE_DIFF and sl < open_p:
                        await connection.modify_position(
                            position_id=pos["id"], 
                            stop_loss=open_p, 
                            take_profit=pos.get("takeProfit", 0)
                        )
                        logging.info(f"[CENT] Break-Even aktivovaný na cene {open_p}")
                        await send_telegram(f"🛡 <b>[CENT] Break-Even aktivovaný</b>\nSL posunutý na vstup: <code>{open_p}</code>")

        except Exception as e:
            logging.error(f"Chyba v cykle: {e}")

        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(run_bot())
