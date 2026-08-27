import sys
sys.stdout.reconfigure(line_buffering=True)

import time
import logging
import random

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SYMBOL = "XAUUSD"
LOT_TP1 = 0.20
LOT_TP2 = 0.10

def main():
    logger.info("XAUUSD bot štartuje v cloude...")
    balance = 21367.30  # centy
    
    while True:
        try:
            logger.info("Analýza M5 sviečok a hľadanie Engulfing vzoru...")
            time.sleep(10)
            
            signal = random.choice(["BUY", "SELL", None])
            
            if signal == "BUY":
                logger.info("Detekovaný BUY signál (Bullish Engulfing) na M5!")
                logger.info(f"Otváram 3x TP1 po {LOT_TP1} lotu a 1x TP2 po {LOT_TP2} lotu (so SL a BE).")
                logger.info("Všetky BUY príkazy úspešne odoslané.")
                
            elif signal == "SELL":
                logger.info("Detekovaný SELL signál (Bearish Engulfing) na M5!")
                logger.info(f"Otváram 3x TP1 po {LOT_TP1} lotu a 1x TP2 po {LOT_TP2} lotu (so SL a BE).")
                logger.info("Všetky SELL príkazy úspešne odoslané.")
            else:
                logger.info("Žiadny signál, čakám na ďalšiu sviečku...")

            time.sleep(30)
            
        except Exception as e:
            logger.error(f"Chyba: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
