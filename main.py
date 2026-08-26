import time
import requests

TOKEN = "r8oq06EiQdSG2bZ7gLuaQLNRdhkwgaxRuUNrQi3pgw"
ACCOUNT_ID = "39ace2a7-8a53-420d-800f-35a9d9feadf2"
SYMBOL = "XAUUSD"

SL = 15
RR = 4
TP = SL * RR

# Použijeme priamy endpoint pre MetaApi REST API
BASE_URL = f"https://mt-client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}"
HEADERS = {
    "auth-token": TOKEN,
    "Content-Type": "application/json"
}

def get_candles():
    url = f"{BASE_URL}/symbols/{SYMBOL}/candles?timeframe=5m&limit=10"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Candles error status {response.status_code}: {response.text}")
    except Exception as e:
        print(f"Error fetching candles: {e}")
    return []

def check_supply_demand_zones(candles):
    if len(candles) < 5:
        return False
    highs = [c['high'] for c in candles[-5:-1]]
    lows = [c['low'] for c in candles[-5:-1]]
    current_price = candles[-1]['close']
    
    zone_high = max(highs)
    zone_low = min(lows)
    
    if zone_low <= current_price <= zone_high:
        return True
    return False

def check_price_action_patterns(candles):
    if len(candles) < 3:
        return False
    
    c2 = candles[-2]
    c3 = candles[-1]
    
    bullish_engulfing = (c2['close'] < c2['open']) and (c3['close'] > c3['open']) and (c3['close'] >= c2['open']) and (c3['open'] <= c2['close'])
    body = abs(c3['close'] - c3['open'])
    total_range = c3['high'] - c3['low']
    lower_shadow = min(c3['open'], c3['close']) - c3['low']
    pin_bar = total_range > 0 and (lower_shadow / total_range > 0.6) and (body / total_range < 0.3)
    inside_bar = (c2['high'] > c2['open'] or c2['high'] > c2['close']) and (c3['high'] <= c2['high']) and (c3['low'] >= c2['low'])
    
    return bullish_engulfing or pin_bar or inside_bar

def manage_positions():
    url = f"{BASE_URL}/positions"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            positions = response.json()
            for pos in positions:
                pos_id = pos.get('id')
                profit = pos.get('profit', 0)
                open_price = pos.get('openPrice')
                sl = pos.get('stopLoss')
                
                if profit > 10 and sl != open_price:
                    mod_url = f"{BASE_URL}/positions/{pos_id}/modify-stop-loss-take-profit"
                    mod_payload = {"stopLoss": open_price, "takeProfit": pos.get('takeProfit')}
                    mod_resp = requests.post(mod_url, json=mod_payload, headers=HEADERS, timeout=10)
                    if mod_resp.status_code == 200:
                        print(f"SUCCESS: Moved Stop Loss to Break-Even for position {pos_id}")
            return len(positions) > 0
    except Exception as e:
        print(f"Error managing positions: {e}")
    return False

def open_trade(signal_type):
    url = f"{BASE_URL}/orders"
    payload = {
        "actionType": "ORDER_TYPE_BUY" if signal_type == "BUY" else "ORDER_TYPE_SELL",
        "symbol": SYMBOL,
        "volume": 0.01,
        "stopLoss": SL,
        "takeProfit": TP
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            print(f"SUCCESS: Opened new {signal_type} trade for {SYMBOL}!")
        else:
            print(f"Failed to open trade: {response.text}")
    except Exception as e:
        print(f"Error opening trade: {e}")

def run():
    print("Market check & position management...")
    if manage_positions():
        print("Active position running. Managing SL/TP and waiting.")
        return
    
    candles = get_candles()
    if not candles:
        print("No candle data received.")
        return
        
    in_zone = check_supply_demand_zones(candles)
    signal = check_price_action_patterns(candles)
    
    if in_zone and signal:
        print("Valid setup found! Opening new trade...")
        open_trade("BUY")

if __name__ == "__main__":
    print("Bot active with fixed REST API connection.")
    while True:
        try:
            run()
        except Exception as e:
            print(f"Main loop error: {e}")
        time.sleep(60)
