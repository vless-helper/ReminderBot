"""Создание/проверка схемы БД.

    python init_db.py

Работает и с PostgreSQL, и с SQLite. В Docker бот создаёт схему сам при
старте (bot.main.init_db), поэтому этот скрипт нужен в основном для
ручной проверки и для стенда.
"""

import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import inspect

from bot.config import config
from bot.db.base import create_engine_and_session
from bot.db.models import Base


def _mask(url: str) -> str:
    """Прячем пароль, чтобы его не утекло в логи."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":")[0]
    return f"{scheme}://{user}:***@{host}"


async def init_database() -> None:
    print(f"Подключение к БД: {_mask(config.DATABASE_URL)}")

    # Каталог нужен только для SQLite
    if config.DATABASE_URL.startswith("sqlite"):
        db_path = config.DATABASE_URL.split("///")[-1]
        if db_path and db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)

    engine, _ = create_engine_and_session(config.DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Таблицы созданы/проверены")

        def _tables(connection):
            return sorted(inspect(connection).get_table_names())

        async with engine.connect() as conn:
            tables = await conn.run_sync(_tables)

        print(f"   Таблицы: {', '.join(tables)}")
        expected = set(Base.metadata.tables)
        missing = expected - set(tables)
        if missing:
            print(f"❌ Отсутствуют таблицы: {', '.join(sorted(missing))}")
            raise SystemExit(1)
        print("✅ Инициализация БД завершена")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_database())
