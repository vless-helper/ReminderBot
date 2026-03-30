import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Токен бота
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # ID админа (можно несколько через запятую)
    ADMIN_IDS: List[int] = field(default_factory=lambda: [
        int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()
    ])
    
    # Настройки базы данных
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db")
    
    # Настройки подписки
    SUBSCRIPTION_PRICE: int = int(os.getenv("SUBSCRIPTION_PRICE", "1000"))
    SUBSCRIPTION_DAYS: int = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
    
    # Настройки напоминаний
    REMINDER_DAYS_BEFORE: int = int(os.getenv("REMINDER_DAYS_BEFORE", "3"))
    
    # За сколько дней напоминать
    REMINDER_DAYS: List[int] = field(default_factory=lambda: [3, 1])   

    # Диапазон часов для отправки уведомлений
    REMINDER_START_HOUR: int = 7
    REMINDER_END_HOUR: int = 21

    # Карта для оплаты
    CARD_NUMBER: str = os.getenv("CARD_NUMBER", "")
    CARD_HOLDER: str = os.getenv("CARD_HOLDER", "")
    
    # Инструкции
    AMNESIA_DOWNLOAD_LINK: str = os.getenv("AMNESIA_DOWNLOAD_LINK", "")
    TUNNEL_INSTRUCTION: str = os.getenv("TUNNEL_INSTRUCTION", "")


config = Config()