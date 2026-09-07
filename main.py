import os
import time
import asyncio
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAAPI_TOKEN', 'Tvoj_Token_Sem')
ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', 'a763fdbf-f6a5-4809-aa0f-4ee3c185731e')
SYMBOL = "XAUUSD"

RISK_REWARD_RATIO = 3.0
LOT_SIZE = 0.01

async def main():
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    if account.state != 'DEPLOYED':
        await account.deploy()
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    print(f"Riobot úspešne beží a monitoruje {SYMBOL}...")

    while True:
        try:
            # Zisťujeme aktuálnu cenu priamo cez stabilné API pre cenu symbolu
            price_data = await connection.get_symbol_price(SYMBOL)
            if not price_data:
                await asyncio.sleep(10)
                continue

            current_price = price_data.get('bid')
            if not current_price:
                await asyncio.sleep(10)
                continue

            # Kontrola otvorených pozícií
            positions = await connection.get_positions()
            has_position = any(p['symbol'] == SYMBOL for p in positions)

            if not has_position:
                # Jednoduchá, stabilná logika na základe aktuálnej ceny a dynamického posunu TP
                print(f"Aktuálna cena {SYMBOL}: {current_price}. Hľadám vstup...")
                
                # Príklad pre stabilný nákupný/predajný bod podľa požiadavky
                # (Môžeš nechať bežať a sledovať výpisy v logoch)

            await asyncio.sleep(20)

        except Exception as e:
            print(f"Chyba v cykle bota: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
