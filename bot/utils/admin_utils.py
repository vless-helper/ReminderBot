from typing import Callable, Optional
from aiogram.types import Message
from bot.config import config

async def require_admin(message: Message) -> bool:
    """Проверка прав админа с отправкой сообщения об ошибке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав для этой команды")
        return False
    return True


def get_args(message: Message, min_args: int = 2, usage: str = None) -> tuple:
    """
    Получает аргументы команды.
    Возвращает (args_list, error_message) или (None, error_message) при ошибке
    """
    parts = message.text.split()
    if len(parts) < min_args + 1:  # +1 потому что команда тоже часть
        error_msg = usage or f"❌ Использование: /command [arg1] [arg2]..."
        return None, error_msg
    return parts[1:], None