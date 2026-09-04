import asyncio
import logging
import os
import traceback

# Konfigurácia logovania
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xauusd_bot")

SYMBOL = os.getenv("SYMBOL", "XAUUSD")
BE_TRIGGER_USD = float(os.getenv("BE_TRIGGER_USD", "10.0"))
LOCKED_PROFIT_OFFSET = float(os.getenv("LOCKED_PROFIT_OFFSET", "2.0"))

async def main():
    logger.info("Spúšťam XAUUSD trading bot na Railway...")
    
    # Testovacie pripojenie / slučka, ktorá sa nevypne
    while True:
        try:
            logger.info("Bot beží a čaká na dáta...")
        except Exception as e:
            logger.error(f"Chyba v cykle: {e}")
            traceback.print_exc()
        
        await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Kritická chyba pri štarte: {e}")
        traceback.print_exc()
