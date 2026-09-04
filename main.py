ahoj import asyncio
import logging
import os

# Konfigurácia logovania, aby bolo všetko vidno v logoch Railway
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xauusd_bot")

# Načítanie premenných z prostredia Railway
SYMBOL = os.getenv("SYMBOL", "XAUUSD")
BE_TRIGGER_USD = float(os.getenv("BE_TRIGGER_USD", "10.0"))
LOCKED_PROFIT_OFFSET = float(os.getenv("LOCKED_PROFIT_OFFSET", "2.0"))

async def manage_open_positions():
    try:
        # Sem si neskôr doplníš reálne volanie MetaApi/MetaTrader pripojenia
        logger.info("Prebieha kontrola pozícií...")
    except Exception as e:
        logger.error(f"Chyba pri správe pozícií: {e}")

async def main():
    logger.info("Spúšťam XAUUSD trading bot na Railway...")
    
    # Nekonečná slučka udrží bota nepretržite nažive a zabráni vypnutiu kontajnera
    while True:
        try:
            await manage_open_positions()
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")
        
        # Pauza 10 sekúnd medzi cyklami, aby bot nezaťažoval systém
        await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kritická chyba pri spúšťaní bota: {e}")
