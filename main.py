import asyncio
import os
from threading import Thread

import pandas as pd
import requests
from flask import Flask
from metaapi_cloud_sdk import MetaApi

SYMBOL = "BTCUSD"
LOT_SIZE = 0.30
PSAR_STEP = 0.02
PSAR_MAX = 0.20
TP_POINTS = 1500.0
SL_POINTS = 1500.0
BE_TRIGGER = 500.0
BE_LOCK = 100.0
LOOP_SECONDS = 10
RECONNECT_SECONDS = 15
COMMENT = "Riobot M1 PSAR ONLY"

M_TOKEN = os.getenv("M_TOKEN")
M_ACC = os.getenv("M_ACC")
T_TOKEN = os.getenv("T_TOKEN")
T_CHAT = os.getenv("T_CHAT")

app = Flask(__name__)


@app.route("/")
def home():
    return "RIObot M1 PSAR is running"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=run_server, daemon=True).start()


def telegram(message):
    if not T_TOKEN or not T_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{T_TOKEN}/sendMessage",
            data={"chat_id": T_CHAT, "text": message},
            timeout=10,
        )
    except Exception as exc:
        print("Telegram error:", exc, flush=True)


def calculate_psar(df):
    if len(df) < 5:
        return df

    out = df.copy().reset_index(drop=True)
    highs = out["high"].astype(float).to_numpy()
    lows = out["low"].astype(float).to_numpy()
    closes = out["close"].astype(float).to_numpy()

    psar = [0.0] * len(out)
    bull = [True] * len(out)

    is_bull = closes[1] >= closes[0]
    af = PSAR_STEP
    ep = highs[0] if is_bull else lows[0]
    psar[0] = lows[0] if is_bull else highs[0]
    bull[0] = is_bull

    for i in
