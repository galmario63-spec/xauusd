import asyncio
import logging
from datetime import datetime, timedelta
from mt5_broker_api import MT5Connection  # Predpokladaná knižnica pre pripojenie

# Nastavenie logovania
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Konfigurácia
SYMBOL = "XAUUSD"
LOT_PER_PART = 0.20  # Alebo podľa tvojho nastavenia

async def main():
    logger.info("XAUUSD reálny obchodný bot štartuje...")
    
    # Pripojenie k brokerovi / MT5 bridge
    connection = MT5Connection()
    await connection.connect()
    
    while True:
        try:
            # 1. Získanie sviečok pre 5-minútový časový rámec (zhodný s M5 grafom)
            candles = await connection.get_historical_candles(
                SYMBOL, '5m',
                datetime.now() - timedelta(hours=2), 5
            )
            
            if len(candles) < 3:
                await asyncio.sleep(10)
                continue
                
            # Logika pre detekciu sviečok (Engulfing)
            prev_candle = candles[-2]
            curr_candle = candles[-1]
            
            bullish_engulfing = (curr_candle['close'] > curr_candle['open'] and 
                                 prev_candle['close'] < prev_candle['open'] and 
                                 curr_candle['close'] >= prev_candle['open'] and 
                                 curr_candle['open'] <= prev_candle['close'])
                                 
            bearish_engulfing = (curr_candle['close'] < curr_candle['open'] and 
                                 prev_candle['close'] > prev_candle['open'] and 
                                 curr_candle['close'] <= prev_candle['open'] and 
                                 curr_candle['open'] >= prev_candle['close'])

            if bullish_engulfing:
                logger.info("Detekovaný BUY signál (Bullish Engulfing na M5)!")
                await connection.create_market_buy_order(
                    symbol=SYMBOL,
                    volume=LOT_PER_PART,
                )
                logger.info("BUY príkaz úspešne odoslaný.")
                
            elif bearish_engulfing:
                logger.info("Detekovaný SELL signál (Bearish Engulfing na M5)!")
                await connection.create_market_sell_order(
                    symbol=SYMBOL,
                    volume=LOT_PER_PART,
                )
                logger.info("SELL príkaz úspešne odoslaný.")
            
            # Kontrola každých 30 sekúnd
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
