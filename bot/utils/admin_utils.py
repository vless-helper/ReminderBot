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

async def check_admin(message: Message) -> bool:
    """
    Проверяет, является ли пользователь администратором.
    Возвращает True если админ, иначе отправляет сообщение об ошибке и возвращает False
    """
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав для этой команды")
        return False
    return True


def admin_required(func: Callable):
    """
    Декоратор для проверки прав администратора
    """
    async def wrapper(message: Message, *args, **kwargs):
        if message.from_user.id not in config.ADMIN_IDS:
            await message.answer("⛔ У вас нет прав для этой команды")
            return
        return await func(message, *args, **kwargs)
    return wrapper


async def parse_user_id_and_text(message: Message, min_parts: int = 3, usage_example: str = None) -> tuple[Optional[int], Optional[str]]:
    """
    Парсит user_id и текст из сообщения.
    Возвращает (user_id, text) или (None, None) если ошибка
    """
    parts = message.text.split(maxsplit=2)
    
    if len(parts) < min_parts:
        if usage_example:
            await message.answer(usage_example)
        else:
            await message.answer(
                "❌ Неверный формат команды.\n"
                "Использование: /command [user_id] [текст]"
            )
        return None, None
    
    try:
        user_id = int(parts[1])
        text = parts[2]
        return user_id, text
    except ValueError:
        await message.answer("❌ Неверный формат ID пользователя")
        return None, None


class AdminCommandHelper:
    """
    Класс-помощник для обработки админских команд
    """
    
    @staticmethod
    async def check_and_parse(message: Message, usage_example: str) -> tuple[bool, Optional[int], Optional[str]]:
        """
        Проверяет права и парсит команду одной функцией.
        Возвращает (is_valid, user_id, text)
        """
        # Проверка прав
        if message.from_user.id not in config.ADMIN_IDS:
            await message.answer("⛔ У вас нет прав для этой команды")
            return False, None, None
        
        # Парсинг команды
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer(usage_example)
            return False, None, None
        
        try:
            user_id = int(parts[1])
            text = parts[2]
            return True, user_id, text
        except ValueError:
            await message.answer("❌ Неверный формат ID пользователя")
            return False, None, None