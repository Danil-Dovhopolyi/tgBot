import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import config
from db.database import create_pool, create_tables
from db.middleware import DbSessionMiddleware
from handlers import user_handlers 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting bot...")

    pool = await create_pool()
    if not pool:
        logger.critical("Failed to create database pool. Exiting.")
        return

    try:
        await create_tables(pool)
    except Exception as e:
        logger.exception(f"Error creating/updating tables: {e}. Exiting.")
        await pool.close()
        return

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DbSessionMiddleware(pool=pool))

    logger.info("Including routers...")
    dp.include_router(user_handlers.router) 

    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.exception(f"Error during polling: {e}")
    finally:
        logger.info("Closing bot session and database pool...")
        await bot.session.close()
        await pool.close()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually")