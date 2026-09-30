"""Типы колонок.

Главная проблема: SQLite не хранит таймзону, поэтому DateTime(timezone=True)
на SQLite возвращает naive-datetime, а на PostgreSQL — aware. Сравнение naive и
aware падает с TypeError, и это ровно тот класс ошибок, из-за которого код
работал у одного разработчика и ломался у других.

UTCDateTime решает это на уровне модели: на записи любой datetime приводится к
aware UTC, на чтении к нему всегда приклеивается UTC. Поэтому в коде везде
безопасно использовать utcnow() из bot.config, независимо от того, какая БД.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def as_utc(value: datetime | None) -> datetime | None:
    """Привести любой datetime к aware UTC. None проходит насквозь."""
    if value is None:
        return None
    if value.tzinfo is None:
        # Считаем наивное время уже заданным в UTC — так его пишет наш код
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime, который всегда отдаёт aware UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect: Dialect):
        return as_utc(value)

    def process_result_value(self, value, dialect: Dialect):
        return as_utc(value)
