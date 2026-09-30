import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy import text

# Значения ниже задаются принудительно: .env в репозитории может содержать
# другие настройки (например, REMINDER_DAYS_BEFORE=3), и тесты молча
# проверяли бы не тот сценарий.
os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["ADMIN_IDS"] = "999, 1000"
os.environ.setdefault("BASE_PRICE", "150")
os.environ["REMINDER_START_HOUR"] = "0"
os.environ["REMINDER_END_HOUR"] = "23"
os.environ.setdefault("REMINDER_TIMEZONE", "Europe/Moscow")
os.environ["REMINDER_DAYS_BEFORE"] = "3,1"

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def pytest_addoption(parser):
    parser.addoption(
        "--postgres-url",
        default=os.getenv("TEST_POSTGRES_URL", ""),
        help="Прогнать тесты на PostgreSQL, например postgresql+asyncpg://user@host/db",
    )


def pytest_generate_tests(metafunc):
    """Каждый тест выполняется и на SQLite, и на PostgreSQL, если он задан."""
    url = metafunc.config.getoption("--postgres-url")
    if not url or "db" not in metafunc.fixturenames:
        return
    metafunc.parametrize("db", [SQLITE_URL, url], indirect=True, ids=["sqlite", "postgres"])


@pytest_asyncio.fixture
async def db(request):
    """Изолированная БД на каждый тест."""
    from bot.db.base import create_engine_and_session
    from bot.db.models import Base

    url = getattr(request, "param", SQLITE_URL)

    if url.startswith("postgresql"):
        # Схему пересоздаём, чтобы тесты не зависели от порядка
        engine, session_maker = create_engine_and_session(url)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield session_maker
        await engine.dispose()
        return

    engine, session_maker = create_engine_and_session(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield session_maker
    await engine.dispose()


@pytest.fixture
def admin_api(monkeypatch):
    from bot.api import client as client_module
    from bot.handlers import admin as admin_module
    from bot.handlers import user as user_module
    from bot.db import crud as crud_module

    from tests.fakes import FakeAdminAPI

    fake = FakeAdminAPI()

    # Патчим admin_api во всех модулях, которые его импортировали,
    # иначе reminder/db продолжат ходить в реальную сеть.
    import importlib
    import pkgutil

    import bot

    targets = [client_module, admin_module, user_module, crud_module]
    for info in pkgutil.walk_packages(bot.__path__, prefix="bot."):
        try:
            targets.append(importlib.import_module(info.name))
        except Exception:  # noqa: BLE001
            continue

    patched = 0
    for module in targets:
        if hasattr(module, "admin_api"):
            monkeypatch.setattr(module, "admin_api", fake)
            patched += 1
    fake.patched_modules = patched

    return fake


@pytest.fixture
def bot(db, admin_api):
    from tests.fakes import FakeBot

    return FakeBot(db)


@pytest.fixture
def users():
    from tests.fakes import FakeUser

    return {
        "admin": FakeUser(id=999, username="boss"),
        "admin2": FakeUser(id=1000, username="boss2"),
        "alice": FakeUser(id=111111, username="alice"),
        "bob": FakeUser(id=222222, username="bob"),
        "carol": FakeUser(id=333333, username="carol"),
    }
