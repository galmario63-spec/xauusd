import asyncio
import os
from metaapi_cloud_sdk import MetaApi

TOKEN = os.getenv('METAPI_TOKEN')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID')

async def main():
    print("Bot štartuje cez oficiálne MetaApi SDK...")
    metaapi = MetaApi(TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    positions = await connection.get_positions()
    print("Aktuálne pozície:", positions)
    
    print("Bot úspešne beží a čaká na dáta...")
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
