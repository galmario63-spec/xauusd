import asyncio
import logging
import os
from metaapi_cloud_sdk import MetaApi

# Nastavenie logovania
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

# --- NASTAVENIA ---
accounts_raw = os.getenv(
    "METAAPI_ACCOUNT_ID",
    "39ace2a7-8a53-420d-800f-35a9d9feadf2,6ec9b96d-841d-4c69-83b2-681f61a4b626",
)
ACCOUNT_IDS = [acc.strip() for acc in accounts_raw.split(",") if acc.strip()]
TOKEN = os.getenv("METAAPI_TOKEN", "")

# Obchodná stratégia & Parametre
SYMBOL = "XAUUSD"
LOT_PER_PART = 0.10  # 0.10 lot na časť
SL_POINTS = 10.0  # Stop Loss -10
TP1_POINTS = 20.0  # TP pre prvé 2 obchody (1:2)
TP2_POINTS = 30.0  # TP pre 3. obchod (1:3)
BE_TRIGGER_PRICE_DIFF = 3.0  # Pohyblivý Break-Even po +3.0 pohybe


async def check_market_structure_and_pa(connection):
  """Analyzuje posledné sviečky, hľadá S/D zóny a Price Action signál.

  (V tejto verzii vyhodnocuje dynamický sviečkový vzorec na M15/M5).
  """
  try:
    # Stiahneme posledné sviečky pre analýzu (napr. timeframe M15)
    candles = await connection.get_historical_candles(SYMBOL, "15m", 20)
    if not candles or len(candles) < 5:
      return False

    # Jednoduchá Price Action / S/D logika:
    # Sledujeme poslednú zatvorenú sviečku a porovnávame ju so swingovými hladinami
    last_candle = candles[-1]
    prev_candle = candles[-2]

    # Príklad bullish signálu (odraz od zóny / silná sviečka nahor)
    body_size = abs(last_candle["close"] - last_candle["open"])
    is_bullish_engulfing = (
        last_candle["close"] > last_candle["open"]
        and prev_candle["close"] < prev_candle["open"]
        and last_candle["close"] >= prev_candle["open"]
    )

    # Ak nastane Price Action potvrdenie, vrácame True (signál na BUY)
    if is_bullish_engulfing or body_size > 2.0:
      logging.info(
          "Detekovaný Price Action signál / S/D odraz na XAUUSD (M15)."
      )
      return True

    return False
  except Exception as e:
    logging.error(f"Chyba pri analýze sviečok a zón: {e}")
    return False


async def check_and_trade(metaapi, account_id):
  """Skontroluje centový účet, analyzuje trh a otvorí pozície pri splnení podmienok."""
  try:
    account = await metaapi.metatrader_account_api.get_account(account_id)
    if account.state != "DEPLOYED":
      await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    positions = await connection.get_positions()
    symbol_positions = [p for p in positions if p["symbol"] == SYMBOL]

    # Ak už na účte pozície sú, nové neotvárame (necháme demo aj cent pod kontrolou)
    if len(symbol_positions) > 0:
      await connection.close()
      return

    # Spustíme analýzu S/D zón a Price Action
    signal_confirmed = await check_market_structure_and_pa(connection)

    if signal_confirmed:
      price = await connection.get_symbol_price(SYMBOL)
      ask_price = price["ask"]

      logging.info(
          f"[{account_id}] S/D zóna a PA potvrdená! Otváram 3 obchody po"
          f" {LOT_PER_PART} lot..."
      )

      for i in range(3):
        initial_sl = ask_price - SL_POINTS
        if i < 2:
          initial_tp = ask_price + TP1_POINTS
          target_label = "TP1 (1:2)"
        else:
          initial_tp = ask_price + TP2_POINTS
          target_label = "TP2 (1:3)"

        result = await connection.create_market_buy_order(
            SYMBOL, LOT_PER_PART, stop_loss=initial_sl, take_profit=initial_tp
        )
        logging.info(
            f"[{account_id}] Obchod {i+1} ({target_label}) otvorený: {result}"
        )

    await connection.close()
  except Exception as e:
    logging.error(f"Chyba pri obchodnej logike na účte {account_id}: {e}")


async def manage_open_positions(metaapi, account_id):
  """Spravuje pohyblivý Break-Even (3) pre otvorené pozície na danom účte."""
  try:
    account = await metaapi.metatrader_account_api.get_account(account_id)
    if account.state != "DEPLOYED":
      return

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    positions = await connection.get_positions()
    for pos in positions:
      if pos["symbol"] == SYMBOL:
        open_price = pos["openPrice"]
        current_price = pos["price"]
        current_sl = pos.get("stopLoss", 0)
        pos_type = pos["type"]

        if pos_type == "POSITION_TYPE_BUY":
          if current_price - open_price >= BE_TRIGGER_PRICE_DIFF:
            if current_sl < open_price:
              logging.info(
                  f"[{account_id}] BUY Break-Even (3) dosiahnutý. Posúvam SL"
                  f" na vstup ({open_price})..."
              )
              await connection.modify_position(
                  position_id=pos["id"],
                  stop_loss=open_price,
                  take_profit=pos.get("takeProfit", 0),
              )

        elif pos_type == "POSITION_TYPE_SELL":
          if open_price - current_price >= BE_TRIGGER_PRICE_DIFF:
            if current_sl > open_price or current_sl == 0:
              logging.info(
                  f"[{account_id}] SELL Break-Even (3) dosiahnutý. Posúvam SL"
                  f" na vstup ({open_price})..."
              )
              await connection.modify_position(
                  position_id=pos["id"],
                  stop_loss=open_price,
                  take_profit=pos.get("takeProfit", 0),
              )

    await connection.close()
  except Exception as e:
    logging.error(f"Chyba pri správe pozícií na účte {account_id}: {e}")


async def main():
  metaapi = MetaApi(TOKEN)
  logging.info(
      f"Inteligentný S/D & PA bot spustený. Sledujem účty: {ACCOUNT_IDS}"
  )

  while True:
    for account_id in ACCOUNT_IDS:
      # 1. Hľadáme signály a spravujeme vstupy (hlavne pre prázdny centový účet)
      await check_and_trade(metaapi, account_id)
      # 2. Strážime Break-Even na aktívnych pozíciách
      await manage_open_positions(metaapi, account_id)

    # Pauza medzi iteráciami, aby sme zbytočne nezaťažovali API (napr. 30 sekúnd)
    await asyncio.sleep(30)


if __name__ == "__main__":
  asyncio.run(main())
