import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from bot.db.models import Base
from bot.config import config

async def init_db():
    engine = create_async_engine(config.DATABASE_URL, echo=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("✅ Таблицы созданы!")

if __name__ == "__main__":
    asyncio.run(init_db())
