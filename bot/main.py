import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import load_config
from bot.handlers import create_google_sheets_router
from bot.sheets import GoogleSheetsClient


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    sheets = GoogleSheetsClient(config)

    bot = Bot(token=config.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_google_sheets_router(config, sheets))

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
