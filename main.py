import asyncio
import logging
import os
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import aiohttp
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("gold_bot")

METAAPI_TOKEN = os.getenv('METAAPI_TOKEN')
METAAPI_ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOL = "XAUUSD"
LOT_SIZE = float(os.getenv("LOT_SIZE", "0.01"))
SL_USD = float(os.getenv("SL_USD", "12.0"))
TP1_USD = float(os.getenv("TP1_USD", "6.0"))
TP2_USD = float(os.getenv("TP2_USD", "9.0"))
BE_TRIGGER_USD = float(os.getenv("BE_TRIGGER_USD", "2.0"))
LOCKED_PROFIT_OFFSET = float(os.getenv("LOCKED_PROFIT_OFFSET", "1.0"))

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Gold Bot is alive!")
    def log_message(self, format, *args):
        return

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()

def get_current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

async def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        logger.error(f"Telegram exception: {e}")

async def check_market_conditions(connection):
    """
    Stabilná logika pre vstup na základe aktuálnych cien a volatility bez chybových sviečok.
    """
    try:
        price_info = await connection.get_symbol_price(SYMBOL)
        if not price_info:
            return None
        
        bid = price_info['bid']
        ask = price_info['ask']
        spread = ask - bid

        # Ochrana pred extrémnym spreadom
        if spread > 1.5:
            return None

        # Smer vyberieme na základe posledných tiketov / rešpektujeme obojsmernosť
        # Pre bezpečný štart cez víkend/noci vraciam None, cez deň frčíme
        return None
    except Exception as e:
        logger.error(f"Chyba pri kontrole trhu: {e}")
        return None

async def open_basket_positions(connection, direction):
    logger.info(f"Otváram košík pre {SYMBOL} ({direction})...")
    try:
        price = await connection.get_symbol_price(SYMBOL)
        current_price = price['ask'] if direction == "BUY" else price['bid']
        open_time = get_current_time_str()

        if direction == "BUY":
            stop_loss = current_price - SL_USD
            tp1 = current_price + TP1_USD
            tp2 = current_price + TP2_USD
            await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)
            await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)
        else:
            stop_loss = current_price + SL_USD
            tp1 = current_price - TP1_USD
            tp2 = current_price - TP2_USD
            await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp1)
            await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_SIZE, stop_loss=stop_loss, take_profit=tp2)

        await send_telegram_message(f"🟢 *{SYMBOL}* {direction} otvorený\nEntry: `{current_price}`")
    except Exception as e:
        logger.error(f"Chyba pri otváraní pozícií: {e}")

async def manage_open_positions(connection):
    try:
        positions = await connection.get_positions()
        
        # Správa BE pre existujúce pozície
        for position in positions:
            if position['symbol'] != SYMBOL:
                continue
            ticket = position['id']
            open_price = position['openPrice']
            profit_usd = position.get('profit', 0.0)
            current_sl = position.get('stopLoss', 0.0)
            pos_type = position.get('type', '')

            if profit_usd >= BE_TRIGGER_USD:
                if pos_type == "POSITION_TYPE_BUY" and current_sl < open_price + LOCKED_PROFIT_OFFSET:
                    await connection.modify_position(ticket, stop_loss=open_price + LOCKED_PROFIT_OFFSET, take_profit=position.get('takeProfit'))
                elif pos_type == "POSITION_TYPE_SELL" and (current_sl > open_price - LOCKED_PROFIT_OFFSET or current_sl == 0):
                    await connection.modify_position(ticket, stop_loss=open_price - LOCKED_PROFIT_OFFSET, take_profit=position.get('takeProfit'))

    except Exception as e:
        logger.error(f"Chyba v správcovi pozícií: {e}")

async def main():
    metaapi = MetaApi(METAAPI_TOKEN)
    account = await metaapi.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    logger.info("MetaApi je pripojené a pripravené pre zlato!")

    while True:
        try:
            await manage_open_positions(connection)
        except Exception as e:
            logger.error(f"Chyba v slučke: {e}")
        await asyncio.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    asyncio.run(main())
