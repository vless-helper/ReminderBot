import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

# Загружаем .env файл
load_dotenv()


@dataclass
class Config:
    # Токен бота
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # ID админа
    ADMIN_IDS: List[int] = field(default_factory=lambda: [
        int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()
    ])
    
    # Настройки базы данных - путь внутри контейнера
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/bot.db")
    
    # Настройки подписки
    SUBSCRIPTION_PRICE: int = int(os.getenv("SUBSCRIPTION_PRICE", "1000"))
    SUBSCRIPTION_DAYS: int = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
    BASE_PRICE: int = int(os.getenv("BASE_PRICE", "150"))
    
    # Настройки напоминаний
    REMINDER_DAYS_BEFORE: int = int(os.getenv("REMINDER_DAYS_BEFORE", "3"))
    REMINDER_START_HOUR: int = int(os.getenv("REMINDER_START_HOUR", "7"))
    REMINDER_END_HOUR: int = int(os.getenv("REMINDER_END_HOUR", "21"))
    
    # Карта для оплаты
    CARD_NUMBER: str = os.getenv("CARD_NUMBER", "")
    CARD_HOLDER: str = os.getenv("CARD_HOLDER", "")
    
    # Инструкции
    AMNESIA_DOWNLOAD_LINK: str = os.getenv("AMNESIA_DOWNLOAD_LINK", "")
    TUNNEL_INSTRUCTION: str = os.getenv("TUNNEL_INSTRUCTION", "")
    
    # Админка Go
    ADMIN_API_URL: str = os.getenv("ADMIN_API_URL", "http://localhost:8080")
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")


config = Config()