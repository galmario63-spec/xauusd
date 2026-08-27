import time
import logging
import MetaTrader5 as mt5

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SYMBOL = "XAUUSD"
LOT_TP1 = 0.20
LOT_TP2 = 0.10
SL_POINTS = 300
BE_TRIGGER = 150

def check_engulfing(candles):
    if len(candles) < 2:
        return None
    prev_c = candles[-2]
    curr_c = candles[-1]
    
    # Podmienky pre Engulfing na M5
    bullish = (curr_c['close'] > curr_c['open'] and 
               prev_c['close'] < prev_c['open'] and 
               curr_c['close'] >= prev_c['open'] and 
               curr_c['open'] <= prev_c['close'])
               
    bearish = (curr_c['close'] < curr_c['open'] and 
               prev_c['close'] > prev_c['open'] and 
               curr_c['close'] <= prev_c['open'] and 
               curr_c['open'] >= prev_c['close'])
               
    if bullish:
        return "BUY"
    elif bearish:
        return "SELL"
    return None

def main():
    logger.info("XAUUSD MT5 centový bot štartuje...")
    
    if not mt5.initialize():
        logger.error(f"MT5 inicializácia zlyhala, chyba: {mt5.last_error()}")
        return

    if not mt5.symbol_select(SYMBOL, True):
        logger.error(f"Nepodarilo sa vybrať symbol {SYMBOL}")
        mt5.shutdown()
        return

    while True:
        try:
            rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, 3)
            if rates is None or len(rates) < 3:
                time.sleep(10)
                continue
                
            candles = [{'open': r['open'], 'close': r['close'], 'high': r['high'], 'low': r['low']} for r in rates]
            signal = check_engulfing(candles)
            
            tick = mt5.symbol_info_tick(SYMBOL)
            if not tick:
                time.sleep(5)
                continue

            if signal == "BUY":
                logger.info("Detekovaný BUY signál (Bullish Engulfing) na M5 – otváram pozície na centovom účte!")
                price = tick.ask
                sl_price = price - (SL_POINTS * 0.1)
                
                # 3x TP1 (0.20 lotu)
                for _ in range(3):
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": SYMBOL,
                        "volume": LOT_TP1,
                        "type": mt5.ORDER_TYPE_BUY,
                        "price": price,
                        "sl": sl_price,
                        "deviation": 20,
                        "magic": 234000,
                        "comment": "XAUUSD TP1",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    mt5.order_send(request)
                    time.sleep(0.3)
                
                # 1x TP2 (0.10 lotu)
                request_tp2 = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": SYMBOL,
                    "volume": LOT_TP2,
                    "type": mt5.ORDER_TYPE_BUY,
                    "price": price,
                    "sl": sl_price,
                    "deviation": 20,
                    "magic": 234000,
                    "comment": "XAUUSD TP2",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                mt5.order_send(request_tp2)
                logger.info("Všetky BUY príkazy úspešne odoslané.")

            elif signal == "SELL":
                logger.info("Detekovaný SELL signál (Bearish Engulfing) na M5 – otváram pozície na centovom účte!")
                price = tick.bid
                sl_price = price + (SL_POINTS * 0.1)
                
                # 3x TP1 (0.20 lotu)
                for _ in range(3):
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": SYMBOL,
                        "volume": LOT_TP1,
                        "type": mt5.ORDER_TYPE_SELL,
                        "price": price,
                        "sl": sl_price,
                        "deviation": 20,
                        "magic": 234000,
                        "comment": "XAUUSD TP1",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    mt5.order_send(request)
                    time.sleep(0.3)
                
                # 1x TP2 (0.10 lotu)
                request_tp2 = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": SYMBOL,
                    "volume": LOT_TP2,
                    "type": mt5.ORDER_TYPE_SELL,
                    "price": price,
                    "sl": sl_price,
                    "deviation": 20,
                    "magic": 234000,
                    "comment": "XAUUSD TP2",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                mt5.order_send(request_tp2)
                logger.info("Všetky SELL príkazy úspešne odoslané.")

            time.sleep(30)

        except Exception as e:
            logger.error(f"Chyba v cykle: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
