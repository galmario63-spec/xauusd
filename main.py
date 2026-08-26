import asyncio
import os
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAPI_TOKEN')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID')
SYMBOL = "XAUUSD"

# Parametre stratégie a rizika
LOT_SIZE = 0.01          # Veľkosť pozície na test
SL_POINTS = 15.0         # Počiatočný Stop Loss (-15 bodov)
TP_POINTS = 60.0         # Take Profit pre pomer 4:1 (15 * 4 = 60 bodov)
BE_TRIGGER = 10.0        # Keď je zisk +10 bodov, posunúť na Break-Even

async def manage_positions(connection):
    """Manažment otvorených pozícií: posun na BE pri +10 zisku"""
    try:
        positions = await connection.get_positions()
        for pos in positions:
            if pos['symbol'] == SYMBOL:
                profit = pos.get('profit', 0)
                open_price = pos['openPrice']
                current_sl = pos.get('stopLoss', 0)
                type_pos = pos['type'] # POSITION_TYPE_BUY alebo POSITION_TYPE_SELL
                
                # Výpočet zisku v bodoch pre XAUUSD
                # (Pre jednoduchosť kontrolujeme profit v dolároch/bodoch podľa nastavenia brokerov)
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
    """Obchodná logika pre vstupy s pomerom 4:1"""
    try:
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

        # --- TU PRICHÁDZA TVOJA ANALÝZA (Supply/Demand / Price Action na M5) ---
        # Pre prvý ostrý test teraz vytvoríme podmienku, ktorá spusti obchod,
        # alebo sem môžeš neskôr presne doplniť svoje zóny.
        # Pre spustenie testu to necháme pripravené – akonáhle nastane signál:
        
        # Príklad ostrého BUY vstupu (môžeš prepnúť na SELL):
        should_buy = False  # Zatiaľ False, kým nepotvrdíš, či chceš hneď otvoriť testovací obchod
        
        if should_buy:
            print("Zaznamenaný signál zo zóny! Otváram ostrú BUY pozíciu...")
            sl = bid - SL_POINTS
            tp = bid + TP_POINTS
            
            await connection.create_market_buy_order(
                symbol=SYMBOL,
                volume=LOT_SIZE,
                stop_loss=sl,
                take_profit=tp
            )
            print("BUY pozícia úspešne otvorená.")
        else:
            print(f"Trh beží. Bid: {bid}, Ask: {ask}. Čakám na potvrdenie zo zóny...")

    except Exception as e:
        print("Chyba v obchodnej logike:", e)

async def main():
    print("Ostrý bot štartuje cez MetaApi SDK...")
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    print("Úspešne pripojené k MetaApi serveru a synchronizované pre obchodovanie.")
    
    while True:
        # 1. Spravuj existujúce pozície (BE / SL)
        await manage_positions(connection)
        
        # 2. Kontroluj trh a exekučnú logiku
        await check_strategy_and_trade(connection)
        
        # Pauza medzi kontrolami
        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main())
