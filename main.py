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
TP1_POINTS = 20.0  # TP pre prvé 2 obchody (1:2 pri SL 10)
TP2_POINTS = 30.0  # TP pre 3. obchod (1:3 pri SL 10)
BE_TRIGGER_PRICE_DIFF = 3.0  # Pohyblivý Break-Even po +3.0 pohybe


async def open_initial_trades(metaapi, account_id):
  """Otvorí 3 obchody iba na prázdnom (centovom) účte s rôznymi TP (1:2 a 1:3)."""
  try:
    account = await metaapi.metatrader_account_api.get_account(account_id)
    if account.state != "DEPLOYED":
      await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    positions = await connection.get_positions()
    symbol_positions = [p for p in positions if p["symbol"] == SYMBOL]

    # Ak už na účte obchody sú, neotvoríme nič (necháme demo tak)
    if len(symbol_positions) > 0:
      logging.info(
          f"[{account_id}] Účet už má otvorené pozície ({len(symbol_positions)}"
          f" ks). Neotváram nové."
      )
      await connection.close()
      return

    # Získame aktuálnu cenu
    price = await connection.get_symbol_price(SYMBOL)
    ask_price = price["ask"]

    logging.info(
        f"[{account_id}] Účet je prázdny. Otváram 3 obchody po {LOT_PER_PART}"
        " lot s rôznym TP..."
    )

    for i in range(3):
      initial_sl = ask_price - SL_POINTS

      # Prvé dva obchody majú TP 1:2 (+20 bodov), tretí obchod má TP 1:3 (+30 bodov)
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
          f"[{account_id}] Obchod {i+1} ({target_label}) úspešné otvorený:"
          f" {result}"
      )

    await connection.close()
  except Exception as e:
    logging.error(f"Chyba pri otváraní obchodov na účte {account_id}: {e}")


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
  logging.info(f"Bot spustený. Sledujem účty: {ACCOUNT_IDS}")

  # 1. Skontrolujeme centový účet a otvoríme obchody s rozdeleným TP
  for account_id in ACCOUNT_IDS:
    await open_initial_trades(metaapi, account_id)

  # 2. Hlavná slučka pre správu Break-Even
  while True:
    for account_id in ACCOUNT_IDS:
      await manage_open_positions(metaapi, account_id)

    await asyncio.sleep(10)


if __name__ == "__main__":
  asyncio.run(main())
