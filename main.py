import time
import requests

TOKEN = "r8oq06EiQdSG2bZ7gLuaQLNRdhkwgaxRuUNrQi3pgw"
ACCOUNT_ID = "39ace2a7-8a53-420d-800f-35a9d9feadf2"
SYMBOL = "XAUUSD"

SL = 15
RR = 4
TP = SL * RR

def get_market_data():
    # Tu sa cez MetaApi sťahujú dáta sviečok (M1/M5)
    # Prepojenie na API prebieha pomocou tokenu a account ID
    return []

def check_supply_demand_zones(candles):
    # Logika pre detekciu Supply a Demand zón
    return False

def check_price_action_patterns(candles):
    # Detekcia 3 hlavných sviečkových formácií (napr. Engulfing, Pinbar, Inside bar)
    return False

def check_pos():
    # Kontrola, či už nie je otvorená pozícia
    return False

def open_trade(signal_type):
    url = f"https://mt-client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/orders"
    headers = {
        "auth-token": TOKEN,
        "Content-Type": "application/json"
    }
    # Príkaz na otvorenie obchodu s XAUUSD, SL a TP
    print(f"Opening {signal_type} trade for {SYMBOL}...")

def run():
    print("Market check (Supply/Demand & Price Action)...")
    if check_pos():
        print("Position already open, waiting.")
        return
    
    candles = get_market_data()
    in_zone = check_supply_demand_zones(candles)
    signal = check_price_action_patterns(candles)
    
    if in_zone and signal:
        print("Valid setup found!")
        open_trade("BUY")

if __name__ == "__main__":
    print("Bot active with Price Action & Zones.")
    while True:
        try:
            run()
        except Exception as e:
            print(e)
        time.sleep(60)
