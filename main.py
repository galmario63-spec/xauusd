import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import asyncio
import logging
from metaapi_cloud_sdk import MetaApi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TOKEN = os.getenv("METAAPI_TOKEN")

SYMBOL = "XAUUSD"

# Konfigurácia lotov a počtu obchodov
LOT_TP1 = 0.30
COUNT_TP1 = 5

LOT_TP2 = 0.20
COUNT_TP2 = 3

SL_POINTS = 350
TP1_POINTS = 400
TP2_POINTS = 800

# Parametre pre Break-Even a zaistenie zisku (v USD na centovom účte)
BE_TRIGGER_USD = 3.00  # Keď zisk na obchode dosiahne 3 USD
BE_LOCK_USD = 1.00     # BE sa posunie na garantovaný zisk 1 USD

async def manage_open_positions(trade_api, account_id):
    try:
        positions = await trade_api.get_positions(account_id)
        
        for position in positions:
            if position['symbol'] != SYMBOL:
                continue
                
            ticket = position['id']
            open_price = position['openPrice']
            profit_usd = position.get('profit', 0)
            current_sl = position.get('stopLoss', 0)
            pos_type = position['type']
            
            # Kontrola pre BUY pozície
            if pos_type == 'POSITION_TYPE_BUY':
                if profit_usd >= BE_TRIGGER_USD and current_sl < open_price:
                    logger.info(f"Pozícia {ticket} dosiahla zisk {profit_usd} USD, posúvam SL na zaistenie zisku.")
                    await trade_api.modify_position(
                        account_id=account_id,
                        position_id=ticket,
                        stop_loss=open_price + 0.10,
                        take_profit=position.get('takeProfit', 0)
                    )
                        
    except Exception as e:
        logger.error(f"Chyba pri manažmente pozícií (SL/BE): {e}")

async def main():
    if not ACCOUNT_ID or not TOKEN:
        logger.error("Chýbajú premenné prostredia METAAPI_ACCOUNT_ID alebo METAAPI_TOKEN.")
        return

    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
        
    # Správne získanie RPC API priamo zo zasa deploynutého objektu account
    trade_api = account.get_rpc_api()

    logger.info("Skript pre riadenie XAUUSD basketu úspešne spustený a beží.")

    while True:
        await manage_open_positions(trade_api, ACCOUNT_ID)
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
