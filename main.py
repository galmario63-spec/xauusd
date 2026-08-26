import asyncio
import os
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAPI_TOKEN')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID')
SYMBOL = "XAUUSD"

# Parametre stratégie a rizika
LOT_SIZE = 0.01          # Veľkość pozície
SL_POINTS = 15.0         # Počiatočný Stop Loss (-15)
TP_POINTS = 60.0         # Take Profit (4:1 pomer -> 60)
BE_TRIGGER = 10.0        # Posun na Break-Even pri zisku +10

async def manage_positions(connection):
    """Manažment otvorených pozícií: posun na BE pri +10 zisku"""
    try:
        positions = await connection.get_positions()
        for pos in positions:
            if pos['symbol'] == SYMBOL:
                profit = pos.get('profit', 0)
                open_price = pos['openPrice']
                current_sl = pos.get('stopLoss', 0)
                
                # Ak zisk dosiahol +10 a SL ešte nie je na Break-Even
                if profit >= BE_TRIGGER and current_sl != open_price:
                    print(f"Dosiahnutý zisk {profit}. Posúvam SL na Break-Even: {open_price}")
                    await connection.modify_position(
                        position_id=pos['id'],
                        stop_loss=open_price,
                        take_profit=pos.get('takeProfit')
                    )
    except Exception as e:
        print("Chyba pri manažmente pozícií:", e)

async def check_strategy_and_trade(connection):
    """Logika pre obchodovanie XAUUSD"""
    try:
        positions = await connection.get_positions()
        # Ak už máme otvorenú pozíciu, nové neotvárame
        if any(p['symbol'] == SYMBOL for p in positions):
            return

        # Získame aktuálnu cenu symbolu priamo cez terminal connection
        price = await connection.get_symbol_price(SYMBOL)
        if not price:
            return
            
        bid = price.get('bid')
        ask = price.get('ask')
        
        if not bid or not ask:
            return

        # Tu beží vyhodnotenie podmienok pre vstup (S&D / Price Action)
        # Pre testovacie účely pripravené na trigger:
        # (Akonáhle budeme chcieť otestovať ostrý vstup, tu doplníme podmienku)
        
        print(f"Trh skontrolovaný. Aktuálna cena XAUUSD - Bid: {bid}, Ask: {ask}. Čakám na signál zo zóny...")

    except Exception as e:
        print("Chyba v obchodnej logike:", e)

async def main():
    print("Bot štartuje cez oficiálne MetaApi SDK...")
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    print("Úspešne pripojené k MetaApi serveru a synchronizované.")
    
    while True:
        # 1. Spravuj existujúce pozície (BE / SL)
        await manage_positions(connection)
        
        # 2. Kontroluj trh a hľadaj vstupy
        await check_strategy_and_trade(connection)
        
        # Pauza medzi kontrolami
        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
