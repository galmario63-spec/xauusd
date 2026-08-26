import asyncio
from metaapi_cloud_sdk import MetaApi

TOKEN = "r8oq06EiQdSG2bZ7gLuaQLNRdhkwgaxRuUNrQi3pgw"
ACCOUNT_ID = "39ace2a7-8a53-420d-800f-35a9d9feadf2"

async def main():
    metaapi = MetaApi(TOKEN)
    print("Connecting to MetaApi...")
    
    # Získanie účtu a jeho zapnutie, ak je vypnutý
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    if account.state != 'DEPLOYED':
        print("Deploying account...")
        await account.deploy()
        
    print("Waiting for API connection...")
    await account.wait_connected()
    
    # Pripojenie cez RPC
    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    
    print("Bot is fully connected and active!")
    
    while True:
        try:
            positions = await connection.get_positions()
            for pos in positions:
                if pos.get('profit', 0) > 10 and pos.get('stopLoss') != pos.get('openPrice'):
                    await connection.modify_position(
                        position_id=pos['id'],
                        stop_loss=pos['openPrice'],
                        take_profit=pos.get('takeProfit')
                    )
                    print(f"SUCCESS: Moved SL to Break-Even for position {pos['id']}")
            
            if positions:
                print(f"Active positions running: {len(positions)}")
            else:
                print("Market check: No active positions.")
                
        except Exception as e:
            print(f"Loop error: {e}")
            
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
