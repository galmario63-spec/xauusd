import asyncio
import logging

# Konfigurácia logovania
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xauusd_bot")

SYMBOL = "XAUUSD"
BE_TRIGGER_USD = 10.0      
LOCKED_PROFIT_OFFSET = 2.0 

async def manage_open_positions(connection):
    try:
        positions = await connection.get_positions()
        for position in positions:
            if position.get('symbol') != SYMBOL:
                continue

            ticket = position['id']
            open_price = position['openPrice']
            profit_usd = position.get('profit', 0.0)
            current_sl = position.get('stopLoss', 0.0)
            pos_type = position['type']

            # Zámok zisku (Break-Even)
            if profit_usd >= BE_TRIGGER_USD:
                if pos_type == "POSITION_TYPE_BUY" and current_sl < open_price:
                    new_sl = open_price + LOCKED_PROFIT_OFFSET
                    await connection.modify_position(ticket, stop_loss=new_sl)
                    logger.info(f"Posunutý SL do zisku pre BUY pozíciu #{ticket}")
                elif pos_type == "POSITION_TYPE_SELL" and (current_sl > open_price or current_sl == 0.0):
                    new_sl = open_price - LOCKED_PROFIT_OFFSET
                    await connection.modify_position(ticket, stop_loss=new_sl)
                    logger.info(f"Posunutý SL do zisku pre SELL pozíciu #{ticket}")

    except Exception as e:
        logger.error(f"Chyba pri správe otvorených pozícií: {e}")

async def main():
    logger.info("Spúšťam XAUUSD trading bot na Railway...")
    
    # Nekonečná slučka, ktorá udrží bota nepretržite v chode
    while True:
        try:
            # Sem môžeš doplniť volanie funkcií, napr.:
            # await manage_open_positions(connection)
            pass
        except Exception as e:
            logger.error(f"Chyba v hlavnej slučke: {e}")
        
        # Pauza 5 sekúnd pred ďalšou kontrolou
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
