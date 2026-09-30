import logging

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def create_engine_and_session(db_url: str):
    """Создаёт engine и sessionmaker для базы данных."""
    kwargs = {"echo": False}

    if db_url.startswith("postgresql"):
        # pre_ping спасает от зависших соединений после рестарта/сети Postgres
        kwargs.update(
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
        )
    elif ":memory:" in db_url:
        # Каждая сессия в :memory: иначе видит свою пустую базу
        from sqlalchemy.pool import StaticPool

        kwargs.update(poolclass=StaticPool, connect_args={"check_same_thread": False})

    engine = create_async_engine(db_url, **kwargs)

    AsyncSessionLocal = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    return engine, AsyncSessionLocal
