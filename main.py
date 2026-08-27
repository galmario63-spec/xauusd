import asyncio
import logging
import os
from datetime import datetime, timedelta
from metaapi_cloud_sdk import MetaApi

# Konfigurácia z premenných prostredia
TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
SYMBOL = "XAUUSD"
LOT_PER_PART = 0.01  # Tvoj zvolený lot

# Nastavenia pre Break-Even (v dolároch)
BE_LOCK_PROFIT_USD = 0.20  # Zaručený zisk

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
            # 1. Získanie sviečok pre analýzu (M1/M5) cez account objekt
            candles = await account.get_historical_candles(SYMBOL, '1m', datetime.now() - timedelta(hours=1), 5)
            if len(candles) < 3:
                await asyncio.sleep(10)
                continue

            prev = candles[-2]  # Predchádzajúca uzatvorená sviečka
            curr = candles[-1]  # Aktuálna sviečka

            # Kontrola otvorených pozícií
            positions = await connection.get_positions()
            has_open_position = len(positions) > 0

            # 2. Správa existujúcich pozícií (Trailing / Break-Even)
            for pos in positions:
                profit = pos.get("profit", 0.0)
                pos_id = pos.get("id")
                pos_type = pos.get("type")
                open_price = pos.get("openPrice")
                current_sl = pos.get("stopLoss", 0.0)

                # Ak zisk dosiahne alebo prekročí limit pre Break-Even
                if profit >= BE_LOCK_PROFIT_USD:
                    if pos_type == "POSITION_TYPE_BUY":
                        desired_sl = open_price + 0.02  # Malý lock nad vstup
                        if current_sl < open_price:
                            logger.info(f"Posúvam BUY pozíciu #{pos_id} na Break-Even/Profit.")
                            await connection.modify_position(
                                position_id=pos_id,
                                stop_loss=desired_sl,
                                take_profit=pos.get("takeProfit")
                            )
                    elif pos_type == "POSITION_TYPE_SELL":
                        desired_sl = open_price - 0.02
                        if current_sl == 0 or current_sl > open_price:
                            logger.info(f"Posúvam SELL pozíciu #{pos_id} na Break-Even/Profit.")
                            await connection.modify_position(
                                position_id=pos_id,
                                stop_loss=desired_sl,
                                take_profit=pos.get("takeProfit")
                            )

            # 3. Stratégia (vstup len ak nie je otvorená pozícia)
            if not has_open_position:
                bullish_engulfing = (
                    prev["close"] < prev["open"] and 
                    curr["close"] > curr["open"] and 
                    curr["close"] >= prev["open"] and 
                    curr["open"] <= prev["close"]
                )

                bearish_engulfing = (
                    prev["close"] > prev["open"] and 
                    curr["close"] < curr["open"] and 
                    curr["close"] <= prev["open"] and 
                    curr["open"] >= prev["close"]
                )

                if bullish_engulfing:
                    logger.info("Detekovaný BUY signál (Bullish Engulfing)!")
                    await connection.create_market_buy_order(
                        symbol=SYMBOL,
                        volume=LOT_PER_PART,
                        stop_loss_rate=round(curr["low"] - 1.5, 2),
                        take_profit_rate=round(curr["close"] + 3.0, 2)
                    )
                    logger.info("BUY príkaz úspešne odoslaný.")

                elif bearish_engulfing:
                    logger.info("Detekovaný SELL signál (Bearish Engulfing)!")
                    await connection.create_market_sell_order(
                        symbol=SYMBOL,
                        volume=LOT_PER_PART,
                        stop_loss_rate=round(curr["high"] + 1.5, 2),
                        take_profit_rate=round(curr["close"] - 3.0, 2)
                    )
                    logger.info("SELL príkaz úspešne odoslaný.")

            await asyncio.sleep(15)

        except Exception as err:
            logger.error(f"Chyba v hlavnej slučke bota: {err}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
