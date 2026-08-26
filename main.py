import asyncio
import requests

# Tvoj token a Account ID
TOKEN = "eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiIxNWUzODNjMTk5Zjc3ZmI4MTA1ODlmMmIzZmE0ZDMyNiIsImFjY2Vzc1J1bGVzIjpbeyJpZCI6InRyYWRpbmctYWNjb3VudC1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsidHJhZGluZy1hY2NvdW50LW1hbmFnZW1lbnQtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVzdC1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcnBjLWFwaSIsIm1ldGhvZHMiOlsibWV0YWFwaS1hcGk6d3M6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVhbC10aW1lLXN0cmVhbWluZy1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOndzOnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJtZXRhc3RhdHMtYXBpIiwibWV0aGhvZHMiOlsibWV0YXN0YXRzLWFwaTpyZXN0OnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJyaXNrLW1hbmFnZW1lbnQtYXBpIiwibWV0aGhvZHMiOlsicmlzay1tYW5hZ2VtZW50LWFwaTpyZXN0OnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJjb3B5ZmFjdG9yeS1hcGkiLCJtZXRob2RzIjpbImNvcHlmYWN0b3J5LWFwaTpyZXN0OnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyIl0sInJlc291cmNlcyI6WyIqOiRVU0VSX0lEJDoqIl19LHsiaWQiOiJtdC1tYW5hZ2VyLWFwaSIsIm1ldGhvZHMiOlsibXQtbWFuYWdlci1hcGk6cmVzdDpkZWFsaW5nOio6KiIsIm10LW1hbmFnZXItYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6ImJpbGxpbmctYXBpIiwibWV0aGhvZHMiOlsiYmlsbGluZy1hcGk6cmVzdDpwdWJsaWM6KjoqIl0sInJvbGVzIjpbInJlYWRlciJdLCJyZXNvdXJjZXMiOlsiKjokVVNFUl9JRCQ6KiJdfV0sImlnbm9yZVJhdGVMaW1pdHMiOmZhbHNlLCJ0b2tlbklkIjoiMjAyMTAyMTMiLCJpbXBlcnNvbmF0ZWQiOmZhbHNlLCJyZWFsVXNlcklkIjoiMTVlMzgzYzE5OWY3N2ZiODEwNTg5ZjJiM2ZhNGQzMj6iLCJpYXQiOjE3ODc3NDE3OCwiZXhwIjoxNzk1NTIyMTc4fQ.HRqqxb3E8OyhospynbO-AtvkVbgQTrZSldld-KFHZ7IKzNyO598Pa9x3RUE84KQ1RUPbcS1WFdg-AmiBcIq-93IDlEBdwc_4uGUt61ndPIn4y_PobraNlZ1BlbV72-W-lrtQi26yLj0tmtWzcurjNv_Y3SizzI1dxpIUxiRm1q9yNxpOU0QXoDgsB4ohNxKMg9_AxifvA7JgDvYd59p6IKLlQv5ylVsQCg6DqNwvGpYsUlEcOn0L45M1HAPzn_ucGOV0_FyezDhOOE7WixFJXctq5L6Nl3a1J6AffTRqRdkPomM5wVdSyy_IHgn7NNqoHViMIwT9GzS54IQyI-22s2dMSUPD9nvw-VCzJB-BFRg1CI1umeoukdplcQSO7FwbbbLHHyBd0s-iqTpsv7_QplhskxTwNBcBbdE-dDzRbeb7fg7-RJatjMp-Zex4KFjaAeL-Q1pMcY30Qqjclb2FuYLAqQM-gdtdPgSPRXMiVCcABYmX4AQPoUWFqe8fU4vpsOY7zvGb4Es8Kqv9f0pwTi4OiUib_8A9Rt5l5CbuHUaQkvjEWRf5GwrTkQGVby0uDwcnu-bX4VsDtTASgFopPIApT2sDt7rFdsUK2Kcf5sME-geDCyQeUXtnwNVEGritQ5jETRmYMTTC5rdKuUpFhhsh4AlsiyYvBqRh4g-icXg"
ACCOUNT_ID = "39ace2a7-8a53-420d-800f-35a9d9feadf2"

HEADERS = {
    "auth-token": TOKEN,
    "Content-Type": "application/json"
}

def main():
    print("Bot is starting via Direct REST API...")
    
    # 1. Skontrolujeme/zapneme účet cez API
    try:
        deploy_res = requests.post(
            f"https://client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/deploy",
            headers=HEADERS,
            timeout=10
        )
        print(f"Deploy status: {deploy_res.status_code}")
    except Exception as e:
        print(f"Deploy warning: {e}")

    while True:
        try:
            # 2. Získame pozície priamo cez MetaApi REST endpoint
            res = requests.get(
                f"https://client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/trade-account/positions",
                headers=HEADERS,
                timeout=10
            )
            
            if res.status_code == 200:
                positions = res.json()
                print(f"Active positions check: {len(positions)}")
                
                for pos in positions:
                    profit = pos.get('profit', 0)
                    open_price = pos.get('openPrice')
                    stop_loss = pos.get('stopLoss')
                    pos_id = pos.get('id')
                    
                    # Ak je zisk > 10 USD a SL ešte nie je na Break-Even
                    if profit > 10 and stop_loss != open_price:
                        mod_url = f"https://client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/trade-account/orders"
                        payload = {
                            "action": "POSITION_MODIFY",
                            "positionId": pos_id,
                            "stopLoss": open_price
                        }
                        mod_res = requests.post(mod_url, headers=HEADERS, json=payload, timeout=10)
                        if mod_res.status_code in [200, 204]:
                            print(f"SUCCESS: Moved SL to Break-Even for position {pos_id}")
                        else:
                            print(f"Failed to modify position: {mod_res.text}")
            else:
                print(f"API response error: {res.status_code} - {res.text}")
                
        except Exception as e:
            print(f"Loop error: {e}")
            
        import time
        time.sleep(60)

if __name__ == "__main__":
    main()
