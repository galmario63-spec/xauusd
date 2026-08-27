import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# XAUUSD - Hlavný skript pre centový účet
np.random.seed(42)
dates = pd.date_range(start="2023-01-01", end="2026-08-27", freq="1h")
prices = 2000 + np.cumsum(np.random.randn(len(dates)) * 2.0)

df = pd.DataFrame({
    'Open': prices + np.random.randn(len(dates)) * 0.5,
    'High': prices + abs(np.random.randn(len(dates)) * 1.5),
    'Low': prices - abs(np.random.randn(len(dates)) * 1.5),
    'Close': prices + np.random.randn(len(dates)) * 0.5
}, index=dates)

# Aktuálny počiatočný vklad v centoch
balance = 4192.30  
equity_curve = [balance]

for i in range(1, len(df)):
    prev = df.iloc[i-1]
    curr = df.iloc[i]
    
    bullish = curr['Close'] > curr['Open'] and prev['Close'] < prev['Open']
    bearish = curr['Close'] < curr['Open'] and prev['Close'] > prev['Open']
    
    if (bullish or bearish) and balance > 500:
        entry_price = curr['Close']
        trade_pnl = 0.0
        
        # Agresívny model: TP1 (3x 0.20 lotu) + TP2 (1x 0.10 lotu) v centoch
        for j in range(i+1, min(i+10, len(df))):
            fc = df.iloc[j]
            if bullish:
                diff = fc['High'] - entry_price
                if diff >= 1.00:
                    trade_pnl += (3 * 0.20 * 1.00 * 100) + (1 * 0.10 * 3.00 * 100)
                    break
                elif diff <= -1.50:
                    trade_pnl -= ((3 * 0.20 + 1 * 0.10) * 1.50 * 100)
                    break
            else:
                diff = entry_price - fc['Low']
                if diff >= 1.00:
                    trade_pnl += (3 * 0.20 * 1.00 * 100) + (1 * 0.10 * 3.00 * 100)
                    break
                elif diff <= -1.50:
                    trade_pnl -= ((3 * 0.20 + 1 * 0.10) * 1.50 * 100)
                    break
                    
        balance += trade_pnl
        equity_curve.append(balance)

plt.figure(figsize=(10, 5))
plt.plot(equity_curve, label="Aktuálny kapitál 4192.30 centov (3x 0.20 | 1x 0.10)", color='blue')
plt.title("XAUUSD Main Script - Centový účet (2023 - 2026)")
plt.xlabel("Počet obchodov")
plt.ylabel("Zostatok v centoch")
plt.legend()
plt.grid(True)
plt.show()

print(f"Konečný stav účtu: {balance:.2f} centov (t.j. {balance/100:.2f} USD)")
