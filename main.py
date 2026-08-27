import asyncio
import logging
from datetime import datetime, timedelta
from mt5_broker_api import MT5Connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SYMBOL = "XAUUSD"
LOT_TP1 = 0.20
LOT_TP2 = 0.10

# Nastavenia pre Stop Loss a Break-Even s ohľadom na centový účet
SL_POINTS = 300       # Stop Loss vzdialenosť
BE_TRIGGER = 150      # Profit v bodoch, pri ktorom aktivujeme BE
BE_LOCK_CENTS = 0.30  # Zafixovaný zisk v USD (30 centov) pri aktivácii BE

async def main():
    logger.info("XAUUSD centový bot (SL + BE s 30 centovým ziskom) štartuje...")
    
    connection = MT5Connection()
    await connection.connect()
    
    while True:
        try:
            candles = await connection.get_historical_candles(
                SYMBOL, '5m',
                datetime.now() - timedelta(hours=2), 5
            )
            
            if len(candles) < 3:
                await asyncio.sleep(10)
                continue
                
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

            current_price = curr_candle['close']

            if bullish_engulfing:
                logger.info("Detekovaný BUY signál (Bullish Engulfing) na centovom účte - otváram pozície!")
                
                sl_price = current_price - (SL_POINTS * 0.1)
                
                # 3x TP1 (0.20 lotu)
                for i in range(3):
                    await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_TP1, stop_loss=sl_price)
                    await asyncio.sleep(0.5)
                
                # 1x TP2 (0.10 lotu)
                await connection.create_market_buy_order(symbol=SYMBOL, volume=LOT_TP2, stop_loss=sl_price)
                
                logger.info("Všetky BUY príkazy úspešne odoslané.")
                
            elif bearish_engulfing:
                logger.info("Detekovaný SELL signál (Bearish Engulfing) na centovom účte - otváram pozície!")
                
                sl_price = current_price + (SL_POINTS * 0.1)
                
                # 3x TP1 (0.20 lotu)
                for i in range(3):
                    await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_TP1, stop_loss=sl_price)
                    await asyncio.sleep(0.5)
                
                # 1x TP2 (0.10 lotu)
                await connection.create_market_sell_order(symbol=SYMBOL, volume=LOT_TP2, stop_loss=sl_price)
                
                logger.info("Všetky SELL príkazy úspešne odoslané.")
            
            # Aplikovanie Break-Even s garanciou minimálneho zisku 30 centov
            await connection.check_and_apply_break_even(
                symbol=SYMBOL, 
                trigger_points=BE_TRIGGER, 
                lock_profit_cents=BE_LOCK_CENTS
            )
            
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
