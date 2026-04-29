import aiohttp
import logging
from typing import Optional, Dict, Any
from bot.config import config

logger = logging.getLogger(__name__)

class AdminAPIClient:
    """Клиент для взаимодействия с админкой на Go"""
    
    def __init__(self):
        self.base_url = config.ADMIN_API_URL
        self.headers = {
            "Content-Type": "application/json"
        }
        
    async def create_user(self, name: str) -> tuple[bool, Optional[str]]:
        """
        Создать пользователя в админке
        POST /users с JSON {"name": "user_123456789"}
        """
        url = f"{self.base_url}/users"
        
        # Подробное логирование
        logger.info(f"=== CREATE USER REQUEST ===")
        logger.info(f"URL: {url}")
        logger.info(f"Headers: {self.headers}")
        logger.info(f"Body: {{'name': '{name}'}}")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json={"name": name}, headers=self.headers) as resp:
                    # Логируем ответ
                    logger.info(f"=== CREATE USER RESPONSE ===")
                    logger.info(f"Status: {resp.status}")
                    response_text = await resp.text()
                    logger.info(f"Response body: {response_text}")
                    
                    if resp.status == 201:
                        logger.info(f"Пользователь {name} создан в админке")
                        return True, "created"
                    elif resp.status == 409:
                        logger.info(f"Пользователь {name} уже существует в админке")
                        return True, "already_exists"
                    else:
                        logger.error(f"Ошибка создания пользователя {name}: {resp.status} - {response_text}")
                        return False, response_text
            except Exception as e:
                logger.error(f"Ошибка соединения с админкой: {e}")
                return False, str(e)


    async def create_user_and_get_config(self, name: str) -> Optional[str]:
        """
        Создать пользователя (или получить существующего) и получить VLESS ссылку
        """
        # Пытаемся создать пользователя
        success, status = await self.create_user(name)
        
        if not success:
            return None
        
        # Если пользователь уже существовал или создан - получаем конфиг
        vless_link = await self.get_vless_link(name)
        return vless_link


    async def get_or_create_user_config(self, name: str) -> Optional[str]:
        """
        Получить конфиг пользователя (создать если не существует)
        """
        # Сначала пробуем получить конфиг
        vless_link = await self.get_vless_link(name)
        
        if vless_link:
            logger.info(f"Конфиг для {name} получен (существующий)")
            return vless_link
        
        # Если конфига нет - создаем пользователя и получаем конфиг
        logger.info(f"Пользователь {name} не найден, создаем...")
        return await self.create_user_and_get_config(name)
    
    async def get_vless_link(self, name: str) -> Optional[str]:
        """
        Получить VLESS ссылку для пользователя
        GET /users/{name}/vless
        """
        url = f"{self.base_url}/users/{name}/vless"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("link")
                    else:
                        logger.error(f"Ошибка получения VLESS для {name}: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"Ошибка соединения с админкой: {e}")
                return None
    
    async def get_singbox_config(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Получить SingBox конфиг для пользователя
        GET /users/{name}/singbox
        """
        url = f"{self.base_url}/users/{name}/singbox"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"Ошибка получения SingBox для {name}: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"Ошибка соединения с админкой: {e}")
                return None
    
    async def get_user(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о пользователе
        GET /users/{name}
        """
        url = f"{self.base_url}/users/{name}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 404:
                        return None
                    else:
                        logger.error(f"Ошибка получения пользователя {name}: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"Ошибка соединения с админкой: {e}")
                return None
    
    async def create_user_and_get_config(self, name: str) -> Optional[str]:
        """
        Создать пользователя и получить VLESS ссылку (удобный метод)
        """
        # Создаем пользователя
        created = await self.create_user(name)
        if not created:
            return None
        
        # Получаем VLESS ссылку
        vless_link = await self.get_vless_link(name)
        return vless_link

    async def create_user_with_number(self, base_name: str, number: int) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Создать пользователя с номером конфига
        Например: base_name = "user_1924089475", number = 1 -> "user_1924089475_1"
        """
        full_name = f"{base_name}_{number}"
        return await self.create_user_and_get_config(full_name)
    
    async def delete_user(self, name: str) -> bool:
        """
        Удалить пользователя из админки
        DELETE /users/{name}
        """
        url = f"{self.base_url}/users/{name}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.delete(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        logger.info(f"Пользователь {name} удален из админки")
                        return True
                    else:
                        logger.error(f"Ошибка удаления пользователя {name}: {resp.status}")
                        return False
            except Exception as e:
                logger.error(f"Ошибка соединения с админкой: {e}")
                return False
    
    async def archive_user(self, name: str) -> bool:
        """
        Архивировать пользователя в админке
        PATCH /users/{name} с JSON {"archived": true}
        """

        url = f"{self.base_url}/users/{name}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.patch(url, json={"archived": True}, headers=self.headers) as resp:
                    if resp.status == 200:
                        logger.info(f"Пользователь {name} успешно заблокирован в админке")
                        return True
                    else:
                        logger.error(f"Ошибка при блокировке пользователя {name}: {resp.status} - {error}")
                        return False                    
            except Exception as e:
                logger.error(f"Ошибка соединения с админкой: {e}")
                return False

    async def unarchive_user(self, name: str) -> bool:
        """
        Разархивировать пользователя в админке
        PATCH /users/{name} с JSON {"archived": false}
        """

        url = f"{self.base_url}/users/{name}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.patch(url, json={"archived": False}, headers=self.headers) as resp:
                    if resp.status == 200:
                        logger.info(f"Пользователь {name} успешно разблокирован в админке")
                        return True
                    else:
                        logger.error(f"Ошибка при разблокировке пользователя {name}: {resp.status} - {error}")
                        return False                    
            except Exception as e:
                logger.error(f"Ошибка соединения с админкой: {e}")
                return False

    async def health_check(self) -> bool:
        """Проверка доступности админки"""
        url = f"{self.base_url}/health"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=self.headers) as resp:
                    return resp.status == 200
            except Exception:
                return False

# Создаем глобальный экземпляр клиента
admin_api = AdminAPIClient()