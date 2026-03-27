from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def create_engine_and_session(db_url: str):
    """Создает engine и sessionmaker для базы данных"""
    engine = create_async_engine(
        db_url,
        echo=False,
    )
    
    AsyncSessionLocal = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    
    return engine, AsyncSessionLocal
