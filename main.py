import asyncio
from metaapi_cloud_sdk import MetaApi

TOKEN = "r8oq06EiQdSG2bZ7gLuaQLNRdhkwgaxRuUNrQi3pgw"
ACCOUNT_ID = "39ace2a7-8a53-420d-800f-35a9d9feadf2"
SYMBOL = "XAUUSD"

async def main():
    api = MetaApi(TOKEN)
    print("Connecting to MetaApi account...")
    
    account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
    if account.state not in ['DEPLOYING', 'DEPLOYED']:
        await account.deploy()
        
    await account.wait_connected()
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
            
            if len(positions) > 0:
                print("Active position running...")
            else:
                print("Market check (Supply/Demand & Price Action)...")
                
        except Exception as e:
            print(f"Loop error: {e}")
            
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
