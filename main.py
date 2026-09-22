import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi


# =========================================================
# RIObot GOLD - NASTAVENIA
# =========================================================

SYMBOL = "XAUUSD"
LOT_SIZE = 1.00

PSAR_STEP = 0.02
PSAR_MAX = 0.20

# Pri tickSize 0.01:
# 800 bodov = 8.00 ceny
# 1000 bodov = 10.00 ceny
TP_POINTS = 800.0
SL_POINTS = 1000.0

# BREAK EVEN:
# pri +5.00 presuň SL na +3.00
BE_TRIGGER = 500.0
BE_LOCK = 300.0

COMMENT = "RIObot GOLD M5 PSAR 2DOT BE"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
META_TIMEOUT = 30


# =========================================================
# ENV
# =========================================================

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")


# =========================================================
# FLASK / RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot GOLD ACTIVE"


def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):
    if not T_TOKEN or not T_CHAT:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={"chat_id": T_CHAT, "text": message},
            timeout=10,
        )
    except Exception as e:
        print(f"TELEGRAM ERROR: {e}", flush=True)


# =========================================================
# PARABOLIC SAR
# =========================================================

def psar_values(df):
    high = df["high"].astype(float).tolist()
    low = df["low"].astype(float).tolist()

    n = len(df)
    if n < 3:
        return [None] * n

    psar = [None] * n

    bull = True
    af = PSAR_STEP
    ep = high[0]
    sar = low[0]
    psar[0] = sar

    for i in range(1, n):
        sar = sar + af * (ep - sar)

        if bull:
            if i >= 2:
                sar = min(sar, low[i - 1], low[i - 2])
            else:
                sar = min(sar, low[i - 1])

            if low[i] < sar:
                bull = False
                sar = ep
                ep = low[i]
                af = PSAR_STEP
            elif high[i] > ep:
                ep = high[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

        else:
            if i >= 2:
                sar = max(sar, high[i - 1], high[i - 2])
            else:
                sar = max(sar, high[i - 1])

            if high[i] > sar:
                bull = True
                sar = ep
                ep = high[i]
                af = PSAR_STEP
            elif low[i] < ep:
                ep = low[i]
                af = min(af + PSAR_STEP, PSAR_MAX)

        psar[i] = sar

    return psar


# =========================================================
# M5 SVIEČKY
# =========================================================

async def get_closed_m5(account):
    candles = await asyncio.wait_for(
        account.get_historical_candles(
            SYMBOL,
            "5m",
            None,
            120,
        ),
        timeout=META_TIMEOUT,
    )

    if not candles or len(candles) < 6:
        return None

    df = pd.DataFrame(candles)

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    # Posledná sviečka môže byť ešte otvorená.
    if len(df) > 1:
        df = df.iloc[:-1].copy()

    if len(df) < 6:
        return None

    df["psar"] = psar_values(df)
    return df


# =========================================================
# PSAR 2. POTVRDENÁ BODKA
# =========================================================

def get_confirmed_signal(df):
    if df is None or len(df) < 4:
        return None

    a = df.iloc[-3]
    b = df.iloc[-2]
    c = df.iloc[-1]

    a_close = float(a["close"])
    b_close = float(b["close"])
    c_close = float(c["close"])

    a_psar = float(a["psar"])
    b_psar = float(b["psar"])
    c_psar = float(c["psar"])

    # BUY:
    # A = PSAR nad cenou
    # B = 1. bodka pod cenou
    # C = 2. bodka pod cenou
    if (
        a_psar > a_close
        and b_psar < b_close
        and c_psar < c_close
    ):
        return "BUY"

    # SELL:
    # A = PSAR pod cenou
    # B = 1. bodka nad cenou
    # C = 2. bodka nad cenou
    if (
        a_psar < a_close
        and b_psar > b_close
        and c_psar > c_close
    ):
        return "SELL"

    return None


# =========================================================
# POZÍCIE
# =========================================================

async
