import asyncio
import os
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAPI_TOKEN', 'tvj_token')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID', 'tvj_account_id')

async def main():
    print("Bot štartuje cez oficiálne MetaApi SDK...")
    try:
        metaapi = MetaApi(TOKEN)
        account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
        
        # Pripojenie k účtu
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()
        
        # Načítanie pozícií
        positions = await connection.get_positions()
        print("Aktuálne pozície:", positions)
        
    except Exception as e:
        print("Chyba pripojenia cez MetaApi SDK:", e)

if __name__ == "__main__":
    asyncio.run(main())
