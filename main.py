import random
import math

print("XAUUSD bot štartuje...")

# Počiatočný vklad v centoch
balance = 4192.30  
equity_curve = [balance]

# Simulácia obchodov bez externých knižníc
price = 2000.0
for i in range(1, 1000):
    price += random.uniform(-2.0, 2.0)
    bullish = random.choice([True, False])
    
    if balance > 500:
        trade_pnl = 0.0
        # Agresívny model: TP1 (3x 0.20 lotu) + TP2 (1x 0.10 lotu)
        success = random.choice([True, False, True]) # Vyššia šanca na úspech
        
        if success:
            # Zásah cieľa (+0.90 USD / 90 centov)
            trade_pnl += (3 * 0.20 * 1.00 * 100) + (1 * 0.10 * 3.00 * 100)
        else:
            # Zásah Stop Lossu (-1.50 USD pre 0.70 lotu)
            trade_pnl -= ((3 * 0.20 + 1 * 0.10) * 1.50 * 100)
            
        balance += trade_pnl
        equity_curve.append(balance)

print(f"Konečný stav účtu: {balance:.2f} centov (t.j. {balance/100:.2f} USD)")
