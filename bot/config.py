import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Переменная окружения {key}={raw!r} должна быть целым числом")


def _env_ids(key: str) -> list[int]:
    raw = os.getenv(key, "")
    ids: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            raise ValueError(f"Переменная окружения {key}={chunk!r} должна содержать только ID")
    return ids


def _env_int_list(key: str, default: str) -> list[int]:
    raw = os.getenv(key, default) or default
    return [int(x) for x in raw.split(",") if x.strip()]


def utcnow() -> datetime:
    """Единственная точка получения «сейчас». Все даты в БД хранятся в UTC aware."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Config:
    # --- Telegram ---
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # --- Telegram админы ---
    ADMIN_IDS: list[int] = field(default_factory=lambda: _env_ids("ADMIN_IDS"))

    # --- Хранилище ---
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@db:5432/reminderbot"
    )

    # --- Деньги ---
    BASE_PRICE: int = _env_int("BASE_PRICE", 150)
    SUBSCRIPTION_DAYS: int = _env_int("SUBSCRIPTION_DAYS", 30)
    STANDARD_MONTH_DAYS: int = _env_int("STANDARD_MONTH_DAYS", 30)
    BULK_THRESHOLD: int = _env_int("BULK_THRESHOLD", 10)
    BULK_DISCOUNT: int = _env_int("BULK_DISCOUNT", 95)
    PERIOD_DISCOUNT_3: int = _env_int("PERIOD_DISCOUNT_3", 95)
    PERIOD_DISCOUNT_6: int = _env_int("PERIOD_DISCOUNT_6", 90)
    PERIOD_DISCOUNT_12: int = _env_int("PERIOD_DISCOUNT_12", 85)
    MAX_EXTEND_MONTHS: int = _env_int("MAX_EXTEND_MONTHS", 12)

    # --- Напоминания ---
    REMINDER_DAYS_BEFORE: list[int] = field(default_factory=lambda: _env_int_list("REMINDER_DAYS_BEFORE", "3,1"))
    REMINDER_START_HOUR: int = _env_int("REMINDER_START_HOUR", 9)
    REMINDER_END_HOUR: int = _env_int("REMINDER_END_HOUR", 21)
    REMINDER_CHECK_INTERVAL: int = _env_int("REMINDER_CHECK_INTERVAL", 600)
    REMINDER_TIMEZONE: str = os.getenv("REMINDER_TIMEZONE", "Europe/Moscow")

    # --- Реквизиты оплаты ---
    CARD_NUMBER: str = os.getenv("CARD_NUMBER", "")
    CARD_HOLDER: str = os.getenv("CARD_HOLDER", "")

    # --- Инструкции ---
    AMNESIA_DOWNLOAD_LINK: str = os.getenv("AMNESIA_DOWNLOAD_LINK", "")
    TUNNEL_INSTRUCTION: str = os.getenv("TUNNEL_INSTRUCTION", "")

    # --- Админка (cli-vless-manager) ---
    ADMIN_API_URL: str = os.getenv("ADMIN_API_URL", "http://localhost:8080")
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")
    ADMIN_API_TIMEOUT: int = _env_int("ADMIN_API_TIMEOUT", 10)

    def __post_init__(self):
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не задан в .env")
        if not self.ADMIN_IDS:
            raise ValueError("ADMIN_IDS не заданы в .env")
        if self.REMINDER_START_HOUR > self.REMINDER_END_HOUR:
            raise ValueError("REMINDER_START_HOUR не может быть больше REMINDER_END_HOUR")
        if not self.REMINDER_DAYS_BEFORE:
            raise ValueError("REMINDER_DAYS_BEFORE не может быть пустым")
        if len(self.period_tiers()) < 1:
            raise ValueError("Нужно минимум один вариант срока оплаты")

    # --- Производные значения ---

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.REMINDER_TIMEZONE)

    def period_tiers(self) -> list[tuple[int, int]]:
        """Доступные сроки: [(месяцев, скидка в процентах)] — 95 означает -5%."""
        tiers = [(1, 100), (3, self.PERIOD_DISCOUNT_3), (6, self.PERIOD_DISCOUNT_6), (12, self.PERIOD_DISCOUNT_12)]
        return [(m, d) for m, d in tiers if m <= self.MAX_EXTEND_MONTHS]

    def is_allowed_period(self, months: int) -> bool:
        return any(m == months for m, _ in self.period_tiers())

    def discount_for_months(self, months: int) -> int:
        """Лучший (самый низкий) процент для срока months.

        Процент — это «сколько платим», поэтому 85 выгоднее, чем 100.
        """
        eligible = [d for m, d in self.period_tiers() if m <= months]
        return min(eligible) if eligible else 100

    def days_for_months(self, months: int) -> int:
        return months * self.STANDARD_MONTH_DAYS

    def local(self, dt: datetime) -> datetime:
        """UTC-aware -> локальное время пользователя (для показа и для границ суток)."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz)

    def to_local_naive(self, dt: datetime) -> datetime:
        """Локальное время без tz — для сравнения «днём равенства» в SQL запросах."""
        return self.local(dt).replace(tzinfo=None)

    def format_dt(self, dt: datetime, with_time: bool = False) -> str:
        if not dt:
            return "—"
        local = self.local(dt)
        return local.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


config = Config()
