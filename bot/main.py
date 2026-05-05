import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from contextlib import asynccontextmanager

from bot.config import config
from bot.handlers import user, admin
from bot.db.base import create_engine_and_session

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def init_db(engine):
    """Создает таблицы при первом запуске"""
    async with engine.begin() as conn:
        from bot.db.models import Base
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Таблицы проверены/созданы")

class BotWithDB(Bot):
    """Кастомный бот с доступом к сессии БД"""
    def __init__(self, *args, session_maker, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_maker = session_maker
    
    @asynccontextmanager
    async def get_db_session(self):
        """Получить сессию БД (контекстный менеджер)"""
        async with self.session_maker() as session:
            try:
                yield session
            finally:
                await session.close()


async def main():
    # Настройка базы данных
    engine, async_session_maker = create_engine_and_session(config.DATABASE_URL)
    
    await init_db(engine)

    # Создаем бота
    bot = BotWithDB(
        token=config.BOT_TOKEN,
        session_maker=async_session_maker
    )
    
    # Создаем диспетчер
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрируем роутеры
    dp.include_router(user.router)
    dp.include_router(admin.router)
    
    # Запускаем бота
    logger.info("Бот запущен!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
