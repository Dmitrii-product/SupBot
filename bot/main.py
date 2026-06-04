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

    shops = config.load_shops()
    bots = [Bot(token=s["bot_token"]) for s in shops]
    shops_by_token = {s["bot_token"]: s for s in shops}

    dp = Dispatcher()
    dp["shops"] = shops_by_token  # injected into handlers as `shops`
    dp.include_router(router)

    logging.info("Starting %d bot(s): %s",
                 len(bots), ", ".join(s["shop_id"] for s in shops))
    await dp.start_polling(*bots)


if __name__ == "__main__":
    asyncio.run(main())
