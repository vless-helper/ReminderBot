# init_db_docker.py
import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot.db.base import create_engine_and_session
from bot.db.models import Base
from bot.config import config

async def init_database():
    """Инициализация базы данных"""
    print(f"Подключение к БД: {config.DATABASE_URL}")
    
    # Создаем директорию для данных если её нет
    os.makedirs("/app/data", exist_ok=True)
    
    engine, _ = create_engine_and_session(config.DATABASE_URL)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Таблицы успешно созданы!")
    
    await engine.dispose()
    print("✅ Инициализация БД завершена!")

if __name__ == "__main__":
    asyncio.run(init_database())