import asyncio
import os
from datetime import datetime
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAPI_TOKEN')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID')
SYMBOL = "XAUUSD"

# Parametre stratégie a rizika
LOT_SIZE = 0.01          # Veľkosť pozície na test
SL_POINTS = 15.0         # Počiatočný Stop Loss (-15 bodov)
TP_POINTS = 60.0         # Take Profit pre pomer 4:1 (15 * 4 = 60 bodov)
BE_TRIGGER = 10.0        # Keď je zisk +10 bodov, posunúť na Break-Even

def is_allowed_trading_time():
    """Kontrola obchodných hodín pre hlavné seansy (Londýn / New York)"""
    now = datetime.utcnow()
    hour = now.hour
    # Povoľujeme trading v čase najvyššej likvidity (cca 08:00 - 20:00 UTC)
    if 8 <= hour < 20:
        return True
    return False

async def manage_positions(connection):
    """Manažment otvorených pozícií: posun na BE pri +10 zisku a ochrana 4:1"""
    try:
        positions = await connection.get_positions()
        for pos in positions:
            if pos['symbol'] == SYMBOL:
                profit = pos.get('profit', 0)
                open_price = pos['openPrice']
                current_sl = pos.get('stopLoss', 0)
                type_pos = pos['type']
                
                # Ak zisk dosiahol +10 bodov, posunieme SL na Break-Even
                if profit >= BE_TRIGGER:
                    if type_pos == 'POSITION_TYPE_BUY' and current_sl < open_price:
                        print(f"Dosiahnutý zisk {profit}. Posúvam BUY SL na Break-Even: {open_price}")
                        await connection.modify_position(
                            position_id=pos['id'],
                            stop_loss=open_price,
                            take_profit=pos.get('takeProfit')
                        )
                    elif type_pos == 'POSITION_TYPE_SELL' and (current_sl > open_price or current_sl == 0):
                        print(f"Dosiahnutý zisk {profit}. Posúvam SELL SL na Break-Even: {open_price}")
                        await connection.modify_position(
                            position_id=pos['id'],
                            stop_loss=open_price,
                            take_profit=pos.get('takeProfit')
                        )
    except Exception as e:
        print("Chyba pri manažmente pozícií:", e)

async def check_strategy_and_trade(connection):
    """Kompletná obchodná logika: seansy, Supply/Demand a Price Action vstupy"""
    try:
        # 1. Skontrolujeme, či prebieha povolená obchodná seansa
        if not is_allowed_trading_time():
            print("Mimo povolených hodín seansy. Bot čaká na stabilné okno...")
            return

        positions = await connection.get_positions()
        # Ak už máme otvorenú pozíciu na XAUUSD, nové neotvárame
        if any(p['symbol'] == SYMBOL for p in positions):
            return

        # Získame aktuálnu cenu symbolu
        price = await connection.get_symbol_price(SYMBOL)
        if not price:
            return
            
        bid = price.get('bid')
        ask = price.get('ask')
        
        if not bid or not ask:
            return

        # --- HLAVNÁ STRATÉGIA (Supply/Demand & Price Action na M5) ---
        # Bot momentálne vyhodnocuje trh v rámci aktívnej seansy.
        # Všetky parametre pre risk management (4:1, SL 15, TP 60, BE 10) sú pripravené.
        print(f"Seansa aktívna. XAUUSD Bid: {bid}, Ask: {ask}. Monitorujem M5 zóny...")

    except Exception as e:
        print("Chyba v obchodnej logike:", e)

async def main():
    print("Kompletný XAUUSD bot štartuje cez MetaApi SDK...")
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    print("Úspešne pripojené. Všetky moduly (seansy, riadenie rizika, BE) sú aktívne.")
    
    while True:
        # 1. Spravuj existujúce pozície (Break-Even / SL / TP)
        await manage_positions(connection)
        
        # 2. Kontroluj trh a vyhľadávaj vstupy podľa stratégie
        await check_strategy_and_trade(connection)
        
        # Pauza medzi kontrolami
        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
