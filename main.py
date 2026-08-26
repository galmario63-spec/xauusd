import time
import requests

TOKEN = "r8oq06EiQdSG2bZ7gLuaQLNRdhkwgaxRuUNrQi3pgw"
ACCOUNT_ID = "39ace2a7-8a53-420d-800f-35a9d9feadf2"
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

