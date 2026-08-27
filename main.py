import asyncio
import logging
import os
from metaapi_cloud_sdk import MetaApi

# Konfigurácia z premenných prostredia (Railway variables)
TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = "XAUUSD"
LOT_PER_PART = 0.01  # Tvoj zvolený lot na centovom účte

# Nastavenia pre Break-Even (v dolároch)
BE_LOCK_PROFIT_USD = 0.20  # Zaručený zisk pri posune SL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    if not TOKEN or not ACCOUNT_ID:
        logger.error("Chýbajú premenné prostredia METAAPI_TOKEN alebo METAAPI_ACCOUNT_ID!")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    # Čakanie na pripojenie účtu
    if account.state != "DEPLOYED":
        logger.info("Nasadzujem účet do cloudu...")
        await account.deploy()
        
    logger.info("Pripájam sa k MetaTrader terminalu...")
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    logger.info("Úspešne pripojené a zsynchronizované!")

    while True:
        try:
            # 1. Získanie sviečok pre analýzu (M5 graf)
            candles = await connection.get_candles(SYMBOL, "M5", 5)
            if len(candles) < 3:
                await asyncio.sleep(10)
                continue

            prev = candles[-2]  # Predchádzajúca sviečka
            curr = candles[-1]  # Aktuálna sviečka

            # Kontrola otvorených pozícií
            positions = await connection.get_positions()
            has_open_position = len(positions) > 0

            # 2. Správa existujúcich pozícií (Break-Even na +0.20 USD)
            for pos in positions:
                profit = pos.get("profit", 0.0)
                pos_id = pos.get("id")
                pos_type = pos.get("type")  # "POSITION_TYPE_BUY" alebo "POSITION_TYPE_SELL"
                open_price = pos.get("openPrice")
                current_sl = pos.get("stopLoss", 0)

                # Ak zisk dosiahne alebo prekročí bod na zabezpečenie +0.20 USD
                if profit >= BE_LOCK_PROFIT_USD:
                    if pos_type == "POSITION_TYPE_BUY":
                        desired_sl = open_price + 0.20
                        if current_sl < open_price:
                            logger.info(f"Dosahuje Break-Even pre BUY pozíciu {pos_id}. Posúvam SL na istých +0.20 USD.")
                    
                    elif pos_type == "POSITION_TYPE_SELL":
                        if current_sl == 0 or current_sl > open_price:
                            logger.info(f"Dosahuje Break-Even pre SELL pozíciu {pos_id}. Posúvam SL na istých +0.20 USD.")

            # 3. Logika pre vstupy (ak nemáme otvorenú pozíciu)
            if not has_open_position:
                prev_body = abs(prev["close"] - prev["open"])
                curr_body = abs(curr["close"] - curr["open"])

                # Signál BUY: Bullish engulfing
                is_prev_bearish = prev["close"] < prev["open"]
                is_curr_bullish = curr["close"] > curr["open"]
                bullish_engulfing = (
                    is_prev_bearish and is_curr_bullish and 
                    curr["close"] >= prev["open"] and curr["open"] <= prev["close"]
                )

                # Signál SELL: Bearish engulfing
                is_prev_bullish = prev["close"] > prev["open"]
                is_curr_bearish = curr["close"] < curr["open"]
                bearish_engulfing = (
                    is_prev_bullish and is_curr_bearish and 
                    curr["close"] <= prev["open"] and curr["open"] >= prev["close"]
                )

                # Získanie aktuálnych cien pre výpočet SL / TP
                tick = await connection.get_symbol_price(SYMBOL)

                if bullish_engulfing:
                    logger.info("Detekovaný BUY signál (Bullish Engulfing)! Otváram nákup...")
                    sl = tick["bid"] - 3.0
                    tp = tick["ask"] + 5.0
                    await connection.create_market_buy_order(
                        SYMBOL, LOT_PER_PART, stop_loss=sl, take_profit=tp
                    )
                    logger.info("BUY príkaz úspešne odoslaný.")

                elif bearish_engulfing:
                    logger.info("Detekovaný SELL signál (Bearish Engulfing)! Otváram predaj...")
                    sl = tick["ask"] + 3.0
                    tp = tick["bid"] - 5.0
                    await connection.create_market_sell_order(
                        SYMBOL, LOT_PER_PART, stop_loss=sl, take_profit=tp
                    )
                    logger.info("SELL príkaz úspešne odoslaný.")

            await asyncio.sleep(15)

        except Exception as err:
            logger.error(f"Chyba v hlavnej slučke bota: {err}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
