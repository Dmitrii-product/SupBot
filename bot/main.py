"""Entry point. Runs every shop's bot from a single process.

    python main.py
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot import config, store
from bot.handlers import router


async def main():
    logging.basicConfig(level=logging.INFO)
    store.init_db()

    shops = store.get_all_shops()

    # One-time migration: import shops.json into the DB if the DB is empty.
    if not shops:
        try:
            legacy = config.load_shops()
            for s in legacy:
                store.upsert_shop(s["shop_id"], s["name"], s["bot_token"], s["manager_chat_id"])
            logging.info("Migrated %d shop(s) from shops.json to database", len(legacy))
            shops = store.get_all_shops()
        except FileNotFoundError:
            pass

    if not shops:
        logging.error(
            "No shops configured. Add shops via the admin UI "
            "(uvicorn admin.app:app --port 8080) or create shops.json."
        )
        return

    bots = [Bot(token=s["bot_token"]) for s in shops]
    shops_by_token = {s["bot_token"]: s for s in shops}

    dp = Dispatcher()
    dp["shops"] = shops_by_token
    dp.include_router(router)

    logging.info("Starting %d bot(s): %s", len(bots), ", ".join(s["shop_id"] for s in shops))
    await dp.start_polling(*bots)


if __name__ == "__main__":
    asyncio.run(main())
