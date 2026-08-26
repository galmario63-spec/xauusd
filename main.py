import time
import requests

TOKEN = "tvoj_token"
ACCOUNT_ID = "tvoj_account_id"
SYMBOL = "XAUUSD"

SL = 15
RR = 4
TP = SL * RR

def check_pos():
    return False

def run():
    print("Market check...")
    if check_pos():
        print("Busy.")
        return
    sig = False
    if sig:
        print("Signal!")

if __name__ == "__main__":
    print("Bot active.")
    while True:
        try:
            run()
        except Exception as e:
            print(e)
        time.sleep(60)
