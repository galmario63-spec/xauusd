import asyncio
import logging
import os
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import aiohttp
from metaapi_cloud_sdk import MetaApi

# Nastavenie logovania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("gold_bot")

# Načítanie premenných prostredia
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

# HTTP server pre Railway health check
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
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"HTTP server beží na porte {port}...")
    server.serve_forever()

def get_current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

async def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    logger.error(f"Chyba pri posielaní Telegram správy: {await response.text()}")
    except Exception as e:
        logger.error(f"Telegram exception: {e}")

async def check_market_conditions_and_fibo(connection):
    """
    Overí sviečkovú štruktúru, vypočíta Fibonacciho korekčné zóny (0.5 / 0.618)
    a potvrdí Price Action (sviečkové potvrdenie) pred vstupom.
    """
    try:
        # Natiahneme si posledné sviečky (napr. M15 alebo H1 timeframe)
        candles = await connection.get_candles(SYMBOL, '15m', 20)
        if not candles or len(candles) < 15:
            return None

        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        closes = [c['close'] for c in candles]
        opens = [c['open'] for c in candles]

        swing_high = max(highs)
        swing_low = min(lows)
        diff = swing_high - swing_low

        if diff == 0:
            return None

        # Kľúčové Fibo hladiny pre návrat ceny
        fibo_50 = swing_high - (diff * 0.5)
        fibo_618 = swing_high - (diff * 0.618)

        current_price = closes[-1]
        prev_open = opens[-2]
        prev_close = closes[-2]

        # Logika pre BUY: Cena v zóne 0.5 - 0.618 + sviečkové potvrdenie (býčia sviečka)
        in_buy_zone = fibo_618 <= current_price <= fibo_50
        bullish_candle = prev_close > prev_open # Jednoduché sviečkové potvrdenie Price Action

        if in_buy_zone and bullish_candle:
            return "BUY"

        # Logika pre SELL: Cena v horných korekčných zónach + medvedia sviečka
        in_sell_zone = fibo_50 <= current_price <= swing_high
        bearish_candle = prev_close < prev_open

        if in_sell_zone and bearish_candle:
            return "SELL"

        return None
    except Exception as e:
        logger.error(f"Chyba pri výpočte Fibo/Price Action: {e}")
        return None

async def open_basket_positions(connection, direction):
    logger.info(f"Otváram 2-obchodný košík pre {SYMBOL} ({direction}) na základe Fibo & Price Action...")
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

        msg = (f"🟢 *{SYMBOL}* {direction} - RIO_ENGINE (Fibo Filer)\n\n"
               f"Entry: `{current_price}`\n"
               f"TP1: `{tp1}`\n"
               f"TP2: `{tp2}`\n"
               f"SL: `{stop_loss}`\n"
               f"Time: `{open_time}`")
        await send_telegram_message(msg)
        logger.info(f"Košík úspešne otvorený pre {SYMBOL}")
    except Exception as e:
        logger.error(f"Chyba pri otváraní pozícií: {e}")

async def manage_open_positions(connection):
    try:
        positions = await connection.get_positions()
        
        # Ak nie sú žiadne otvorené pozície, hľadáme nový vstup cez Fibo & PA
        if not any(p['symbol'] == SYMBOL for p in positions):
            direction = await check_market_conditions_and_fibo(connection)
            if direction:
                await open_basket_positions(connection, direction)
            return

        # Správa už otvorených pozícií (Break-Even ochrana)
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
                    new_sl = open_price + LOCKED_PROFIT_OFFSET
                    await connection.modify_position(ticket, stop_loss=new_sl, take_profit=position.get('takeProfit'))
                    logger.info(f"Posúvam SL do zisku pre BUY pozíciu #{ticket}")
                elif pos_type == "POSITION_TYPE_SELL" and (current_sl > open_price - LOCKED_PROFIT_OFFSET or current_sl == 0):
                    new_sl = open_price - LOCKED_PROFIT_OFFSET
                    await connection.modify_position(ticket, stop_loss=new_sl, take_profit=position.get('takeProfit'))
                    logger.info(f"Posúvam SL do zisku pre SELL pozíciu #{ticket}")

    except Exception as e:
        logger.error(f"Chyba v správcovi pozícií: {e}")

async def main():
    logger.info(f"Spúšťam MetaApi prepojenie pre {SYMBOL}...")
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        logger.critical("Chýbajú MetaApi premenné!")
        return

    metaapi = MetaApi(METAAPI_TOKEN)
    account = await metaapi.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    logger.info("MetaApi je pripojené a synchronizované pre zlato (Fibo režim)!")

    while True:
        try:
            await manage_open_positions(connection)
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")
            traceback.print_exc()
        
        await asyncio.sleep(15)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()

    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kritická chyba pri štarte: {e}")
        traceback.print_exc()
