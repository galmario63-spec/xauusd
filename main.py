import asyncio
from metaapi_cloud_sdk import MetaApi

# Presný a čistý JWT token bez akýchkoľvek skrytých znakov
TOKEN = "eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiIxNWUzODNjMTk5Zjc3ZmI4MTA1ODlmMmIzZmE0ZDMyNiIsImFjY2Vzc1J1bGVzIjpbeyJpZCI6InRyYWRpbmctYWNjb3VudC1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsidHJhZGluZy1hY2NvdW50LW1hbmFnZW1lbnQtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVzdC1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcnBjLWFwaSIsIm1ldGhvZHMiOlsibWV0YWFwaS1hcGk6d3M6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVhbC10aW1lLXN0cmVhbWluZy1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOndzOnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJtZXRhc3RhdHMtYXBpIiwibWV0aG9kcyI6WyJtZXRhdGF0cy1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciIsIndyaXRlciJdLCJyZXNvdXJjZXMiOlsiKjokVVNFUl9JRCQ6KiJdfSx7ImlkIjoicmlzay1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsicmlzay1tYW5hZ2VtZW50LWFwaTpyZXN0OnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJjb3B5ZmFjdG9yeS1hcGkiLCJtZXRob2RzIjpbImNvcHlmYWN0b3J5LWFwaTpyZXN0OnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJtdC1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsibXQtbWFuYWdlci1hcGk6cmVzdDpkZWFsaW5nOio6KiIsIm10LW1hbmFnZXItYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6ImJpbGxpbmctYXBpIiwibWV0aG9kcyI6WyJiaWxsaW5nLWFwaTpyZXN0OnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19XSwiaWdub3RlUmF0ZUxpbWl0cyI6ZmFsc2UsInRva2VuSWQiOiIyMDIxMDIxMyIsImltcGVyc29uYXRlZCI6ZmFsc2UsInJlYWxVc2VySWQiOiIxNWUzODNjMTk5Zjc3ZmI4MTA1ODlmMmIzZmE0ZDMyNiIsImlhdCI6MTc4Nzc0NTQ5MiwiZXhwIjoxNzk1NTIxNDkyfQ.DHmn7i2pL96rFVOdjFii0VCrs9caACuWKz2cg9n1_RuGacByXUy_D-0goobT_lzFfnXGsWoFKYKHAA5MuQ2GOiY9q4Xdf-xXfY2H3PSNnG_jJrXyXs1HlWcsSKf4mg820DbEgPyeHwnjX1jrCFGFkm4MmTPqr3SZuKxdwZpefhaMR26KA6IgLRfdJ9ORDUZKU0mtXw5RVegCNcXexV1Ho2qu8Q0P3acIPckltYYW6BMczJ2eW78IikPqzsgggr3xBRiy6vwaF8rGu71HO7tlrAEJquNWFqutNIXLx2bH_d5qdlkifcHua4nTwO3TGdwKqdU6dZ7rzPWMROnsRp3PF4aLUeGeFGhWo150pXiZ-hVpmO_abk_goaSKla0IUmiKuUfQrmXRkSVQUV64_bWVa0sJEwnifkPe6Sf5gfP7gXcT64OTigOR8PQLBt169CF3p4C48bsSIme3AJMGI6RVaU3ETI3rYQdxtEtan11fk89AoRhfChduUsutORdOYgUgFq7C-2DMVg8YqVrk-r99--OF5NbDDb6xOHLw3wKdJBe5PkWKQD1Xb822Hh_eXIRydcTwtM6zqwPRu8GjFVR3QgRME2tWiLrkniMlpXsUqDh0wEzEtNTvmXLLHXIPhepvgsWPP55Mq4tBP7Z6acAxlI8iJPllLtfqTH_jbVJhjII"
ACCOUNT_ID = "39ace2a7-8a53-420d-800f-35a9d9feadf2"

async def main():
    metaapi = MetaApi(TOKEN)
    print("Connecting to MetaApi...")
    
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)
    if account.state != 'DEPLOYED':
        print("Deploying account...")
        await account.deploy()
        
    print("Waiting for API connection...")
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
            
            if positions:
                print(f"Active positions running: {len(positions)}")
            else:
                print("Market check: No active positions.")
                
        except Exception as e:
            print(f"Loop error: {e}")
            
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
