"""Проверка живости для Docker HEALTHCHECK: подключается к БД и делает SELECT 1."""

import asyncio
import sys

from sqlalchemy import text

from bot.config import config
from bot.db.base import create_engine_and_session


async def check() -> int:
    engine, _ = create_engine_and_session(config.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"db unreachable: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(check()))
