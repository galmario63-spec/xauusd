import asyncio
import logging
from metaapi_cloud_sdk import MetaApi

# Nastavenie logovania
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- NASTAVENIA ---
# Z tu zadefinovaných premenných alebo Railway environment premenných načítame ID účtov
# Ak sú oddelené čiarkou, bot ich spracuje naraz obidva.
import os

accounts_raw = os.getenv(
    "METAAPI_ACCOUNT_ID",
    "39ace2a7-8a53-420d-800f-35a9d9feadf2,6ec9b96d-841d-4c69-83b2-681f61a4b626",
)
ACCOUNT_IDS = [acc.strip() for acc in accounts_raw.split(",") if acc.strip()]
TOKEN = os.getenv("METAAPI_TOKEN", "TVOJ_API_TOKEN_SEM")  # alebo si ho ťahá z env

# Obchodná stratégia & Parametre
SYMBOL = "XAUUSD"
LOT_PER_PART = 0.01  # Veľkosť jednej časti
PARTS_COUNT = 3  # Otvoríme 3 pozície naraz na signál
BE_TRIGGER_PRICE_DIFF = 5.0  # Break-even posun na vstup po +5.0 USD pohybe


async def execute_trade_on_account(metaapi, account_id):
    try:
        logging.info(
            f"Pripájam sa k účtu: {account_id} pre vykonanie obchodu..."
        )
        account = await metaapi.metatrader_account_api.get_account(account_id)
        if account.state != "DEPLOYED":
            logging.info(f"Účet {account_id} nie je nasadený, nasadzujem...")
            await account.deploy()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        # Získanie aktuálnej ceny
        price = await connection.get_symbol_price(SYMBOL)
        ask_price = price["ask"]
        logging.info(f"Aktuálna Ask cena pre {SYMBOL} na účte {account_id}: {ask_price}")

        # Otvorenie 3 pozícií paralelne (split na 3 časti)
        for i in range(PARTS_COUNT):
            logging.info(
                f"[{account_id}] Otváram pozíciu {i+1}/{PARTS_COUNT} (BUY {LOT_PER_PART} lot)..."
            )
            result = await connection.create_market_buy_order(
                SYMBOL, LOT_PER_PART, stop_loss=0, take_profit=0
            )
            logging.info(f"[{account_id}] Pozícia {i+1} úspešne otvorená: {result}")

        await connection.close()

    except Exception as e:
        logging.error(f"Chyba pri obchodovaní na účte {account_id}: {e}")


async def manage_open_positions(metaapi, account_id):
    try:
        account = await metaapi.metatrader_account_api.get_account(account_id)
        if account.state != "DEPLOYED":
            return

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        positions = await connection.get_positions()
        for pos in positions:
            if pos["symbol"] == SYMBOL and pos["type"] == "POSITION_TYPE_BUY":
                open_price = pos["openPrice"]
                current_price = pos["price"]
                current_sl = pos.get("stopLoss", 0)

                # Kontrola Break-Even (+5 USD/bodov pohybu nahor)
                if current_price - open_price >= BE_TRIGGER_PRICE_DIFF:
                    if current_sl < open_price:  # ak ešte nie je posunutý na BE
                        logging.info(
                            f"[{account_id}] Dosiahnutý zisk >= {BE_TRIGGER_PRICE_DIFF}. Posúvam SL na vstup ({open_price})..."
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
        f"Bot spustený pre multi-account režim. Sledujem účty: {ACCOUNT_IDS}"
    )

    while True:
        # Tu beží hlavná slučka – prejde každý účet v zozname
        for account_id in ACCOUNT_IDS:
            # Tu by bola tvoja podmienka na vstup (signál)
            # Pre ukážku teraz beží kontrola otvorených pozícií a správa BE
            await manage_open_positions(metaapi, account_id)

        # Pauza medzi kontrolami (napr. 10 sekúnd)
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
