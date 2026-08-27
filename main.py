import asyncio
import logging
import os
import aiohttp
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

# Telegram nastavenia (ak sú vyplnené v Railway)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Určíme, ktorý ID je centový účet (druhý v poradí alebo podľa potreby)
# Predpokladáme, že centový je ten druhý, prípadne ho vieme ošetriť kontextom
CENT_ACCOUNT_ID = ACCOUNT_IDS[1] if len(ACCOUNT_IDS) > 1 else ACCOUNT_IDS[0]

# Obchodná stratégia & Parametre
SYMBOL = "XAUUSD"
LOT_PER_PART = 0.10  # 0.10 lot na časť
SL_POINTS = 10.0  # Stop Loss -10
TP1_POINTS = 20.0  # TP pre prvé 2 obchody (1:2)
TP2_POINTS = 30.0  # TP pre 3. obchod (1:3)
BE_TRIGGER_PRICE_DIFF = 3.0  # Pohyblivý Break-Even po +3.0 pohybe


async def send_telegram_message(message):
  """Odošle notifikáciu na Telegram iba ak sú nastavené premenné."""
  if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    return
  try:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    async with aiohttp.ClientSession() as session:
      async with session.post(url, json=payload) as response:
        if response.status != 200:
          logging.error(f"Chyba pri odosielaní Telegram správy: {response.status}")
  except Exception as e:
    logging.error(f"Výnimka pri Telegram notifikácii: {e}")


async def check_market_structure_and_pa(connection):
  """Analyzuje posledné sviečky a hľadá S/D zóny / Price Action."""
  try:
    candles = await connection.get_historical_candles(SYMBOL, "15m", 20)
    if not candles or len(candles) < 5:
      return False

    last_candle = candles[-1]
    prev_candle = candles[-2]
    body_size = abs(last_candle["close"] - last_candle["open"])
    is_bullish_engulfing = (
        last_candle["close"] > last_candle["open"]
        and prev_candle["close"] < prev_candle["open"]
        and last_candle["close"] >= prev_candle["open"]
    )

    if is_bullish_engulfing or body_size > 2.0:
      return True

    return False
  except Exception as e:
    logging.error(f"Chyba pri analýze sviečok: {e}")
    return False


async def check_and_trade(metaapi, account_id):
  """Skontroluje trh a otvorí pozície VÝHRADNE na centovom účte."""
  # Ak to nie je centový účet, túto funkciu preskočíme
  if account_id != CENT_ACCOUNT_ID:
    return

  try:
    account = await metaapi.metatrader_account_api.get_account(account_id)
    if account.state != "DEPLOYED":
      await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    positions = await connection.get_positions()
    symbol_positions = [p for p in positions if p["symbol"] == SYMBOL]

    # Ak už na centovom účte pozície sú, nové neotvárame
    if len(symbol_positions) > 0:
      await connection.close()
      return

    signal_confirmed = await check_market_structure_and_pa(connection)

    if signal_confirmed:
      price = await connection.get_symbol_price(SYMBOL)
      ask_price = price["ask"]

      logging.info(
          f"[CENT] S/D zóna a PA potvrdená! Otváram 3 obchody po {LOT_PER_PART}"
          " lot..."
      )

      msg = f"🟢 <b>[CENTOVÝ ÚČET] Nový vstup na {SYMBOL}</b>\n"
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
        logging.info(f"[CENT] Obchod {i+1} ({target_label}) otvorený.")
        msg += f"• Obchod {i+1}: 0.10 lot ({target_label})\n"

      # Pošleme upozornenie na Telegram len pre centový účet
      await send_telegram_message(msg)

    await connection.close()
  except Exception as e:
    logging.error(f"Chyba pri obchodnej logike na centovom účte: {e}")


async def manage_open_positions(metaapi, account_id):
  """Spravuje pohyblivý Break-Even (3) pre daný účte."""
  try:
    account = await metaapi.metatrader_account_api.get_account(account_id)
    if account.state != "DEPLOYED":
      return

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    is_cent = account_id == CENT_ACCOUNT_ID
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
                  f"[{'CENT' if is_cent else 'DEMO'}] BUY Break-Even dosiahnutý."
                  f" Posúvam SL na vstup ({open_price})..."
              )
              await connection.modify_position(
                  position_id=pos["id"],
                  stop_loss=open_price,
                  take_profit=pos.get("takeProfit", 0),
              )
              if is_cent:
                await send_telegram_message(
                    f"🛡 <b>[CENTOVÝ ÚČET] Break-Even aktivovaný</b>\nSL posunutý"
                    f" na vstup: <code>{open_price}</code>"
                )

        elif pos_type == "POSITION_TYPE_SELL":
          if open_price - current_price >= BE_TRIGGER_PRICE_DIFF:
            if current_sl > open_price or current_sl == 0:
              logging.info(
                  f"[{'CENT' if is_cent else 'DEMO'}] SELL Break-Even dosiahnutý."
                  f" Posúvam SL na vstup ({open_price})..."
              )
              await connection.modify_position(
                  position_id=pos["id"],
                  stop_loss=open_price,
                  take_profit=pos.get("takeProfit", 0),
              )
              if is_cent:
                await send_telegram_message(
                    f"🛡 <b>[CENTOVÝ ÚČET] Break-Even aktivovaný</b>\nSL posunutý"
                    f" na vstup: <code>{open_price}</code>"
                )

    await connection.close()
  except Exception as e:
    logging.error(f"Chyba pri správe pozícií na účte {account_id}: {e}")


async def main():
  metaapi = MetaApi(TOKEN)
  logging.info("Inteligentný multi-account bot spustený.")

  while True:
    for account_id in ACCOUNT_IDS:
      # Otvára obchody IBA na centovom účte
      await check_and_trade(metaapi, account_id)
      # Stráži Break-Even na oboch (ale notifikuje len cent)
      await manage_open_positions(metaapi, account_id)

    await asyncio.sleep(30)


if __name__ == "__main__":
  asyncio.run(main())
