import asyncio
import logging
import sys
from contextlib import asynccontextmanager, suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from bot.api.client import admin_api
from bot.config import config
from bot.db import crud
from bot.db.base import create_engine_and_session
from bot.handlers import admin, user
from bot.reminder import reminder_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# FSM хранит только ввод пользователя (текст вопроса), платежи отсюда не читаются,
# поэтому MemoryStorage достаточно — состояние не должно переживать рестарт.
STORAGE = MemoryStorage()


class BotWithDB(Bot):
    """Бот с доступом к фабрике сессий БД."""

    def __init__(self, *args, session_maker, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_maker = session_maker

    @asynccontextmanager
    async def get_db_session(self):
        async with self.session_maker() as session:
            try:
                yield session
            finally:
                await session.close()


async def init_db(engine) -> None:
    from bot.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Схема БД проверена")


async def main() -> None:
    engine, session_maker = create_engine_and_session(config.DATABASE_URL)
    reminder_task: asyncio.Task | None = None
    bot: BotWithDB | None = None

    try:
        await init_db(engine)

        # Платёж, оставшийся в processing, означает, что бот упал в момент
        # обработки. Возвращаем его в очередь, иначе он зависнет навсегда.
        async with session_maker() as session:
            recovered = await crud.reset_stuck_payments(session)
            if recovered:
                logger.warning(
                    "Возвращено в очередь незавершённых платежей: %d", recovered
                )

        bot = BotWithDB(
            token=config.BOT_TOKEN,
            session_maker=session_maker,
            default=DefaultBotProperties(parse_mode="HTML"),
        )

        if not await admin_api.health_check():
            logger.error(
                "Админка недоступна по %s — бот запущен, но выдача конфигов "
                "и блокировка не сработают",
                config.ADMIN_API_URL,
            )

        dp = Dispatcher(storage=STORAGE)
        dp.include_router(admin.router)
        dp.include_router(user.router)

        await bot.delete_webhook(drop_pending_updates=True)

        reminder_task = asyncio.create_task(reminder_loop(bot, session_maker))
        logger.info("Бот запущен, админов: %d", len(config.ADMIN_IDS))

        await dp.start_polling(bot)
    finally:
        # Сессии должны закрываться даже если бот упал на старте,
        # иначе остаются "Unclosed client session" в логах
        if reminder_task is not None:
            reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await reminder_task
        if bot is not None:
            await bot.session.close()
        await admin_api.close()
        await engine.dispose()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
