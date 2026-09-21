import asyncio
import os
import traceback
from flask import Flask
from threading import Thread

import pandas as pd
import requests
from metaapi_cloud_sdk import MetaApi


# =========================================================
# NASTAVENIA
# =========================================================

SYMBOL = "BTCUSD"

# CENTOVÝ ÚČET
LOT_SIZE = 0.30

# PARABOLIC SAR
PSAR_STEP = 0.02
PSAR_MAX = 0.20

# TP / SL 1:1
TP_POINTS = 1500.0
SL_POINTS = 1500.0

# BREAK EVEN
BE_TRIGGER = 500.0
BE_LOCK = 100.0

COMMENT = "Riobot M1 PSAR ONLY"

LOOP_SECONDS = 10
RECONNECT_SECONDS = 15

# Po koľkých chybách v loope spravíme nové spojenie
MAX_CONNECTION_ERRORS = 2


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
    return "RIObot M1 PSAR ONLY is running"


@app.route("/health")
def health():
    return "OK"


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port
    )


def keep_alive():
    thread = Thread(
        target=run_server,
        daemon=True
    )
    thread.start()


# =========================================================
# TELEGRAM
# =========================================================

def
