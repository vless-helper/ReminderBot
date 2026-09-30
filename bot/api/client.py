import logging
from typing import Any, Optional

import aiohttp

from bot.config import config

logger = logging.getLogger(__name__)


class AdminAPIError(Exception):
    """Ошибка обращения к админке: несогласованное состояние между ботом и cli-vless-manager."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class AdminAPIClient:
    """Клиент cli-vless-manager (Go). Одна aiohttp-сессия на всё время работы бота."""

    def __init__(self):
        self.base_url = config.ADMIN_API_URL.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=config.ADMIN_API_TIMEOUT)
        self._session: Optional[aiohttp.ClientSession] = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if config.ADMIN_API_KEY:
            headers["Authorization"] = f"Bearer {config.ADMIN_API_KEY}"
        return headers

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(self, method: str, path: str, **kwargs) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        client = await self.session()
        try:
            async with client.request(method, url, headers=self._headers(), **kwargs) as resp:
                body = await resp.text()
                try:
                    parsed = await resp.json(content_type=None) if body else None
                except Exception:
                    parsed = body
                return resp.status, parsed
        except aiohttp.ClientError as e:
            raise AdminAPIError(f"Админка недоступна ({url}): {e}") from e

    # --- Пользователи ---

    async def create_user(self, name: str) -> tuple[bool, str]:
        """POST /users. Возвращает (ok, 'created'|'already_exists'|ошибка)."""
        status, body = await self._request("POST", "/users", json={"name": name})
        if status == 201:
            logger.info("Пользователь %s создан в админке", name)
            return True, "created"
        if status == 409:
            logger.info("Пользователь %s уже существует в админке", name)
            return True, "already_exists"
        raise AdminAPIError(f"Не удалось создать {name}: {status} {body}", status)

    async def get_vless_link(self, name: str) -> Optional[str]:
        """GET /users/{name}/vless. None, если пользователя нет."""
        status, body = await self._request("GET", f"/users/{name}/vless")
        if status == 200:
            return body.get("link")
        if status == 404:
            return None
        raise AdminAPIError(f"Не удалось получить VLESS для {name}: {status} {body}", status)

    async def get_user(self, name: str) -> Optional[dict[str, Any]]:
        status, body = await self._request("GET", f"/users/{name}")
        if status == 200:
            return body
        if status == 404:
            return None
        raise AdminAPIError(f"Не удалось получить {name}: {status} {body}", status)

    async def get_all_users(self) -> list[dict[str, Any]]:
        status, body = await self._request("GET", "/users")
        if status != 200:
            raise AdminAPIError(f"Не удалось получить список пользователей: {status} {body}", status)
        return body or []

    async def delete_user(self, name: str) -> bool:
        """DELETE /users/{name}. True, если удалён или его уже не было."""
        status, body = await self._request("DELETE", f"/users/{name}")
        if status == 200:
            logger.info("Пользователь %s удален из админки", name)
            return True
        if status == 404:
            logger.info("Пользователь %s уже отсутствовал в админке", name)
            return True
        raise AdminAPIError(f"Не удалось удалить {name}: {status} {body}", status)

    async def set_archived(self, name: str, archived: bool) -> bool:
        """PATCH /users/{name} {"archived": bool} — вкл/выкл доступ к VPN."""
        status, body = await self._request("PATCH", f"/users/{name}", json={"archived": archived})
        if status == 200:
            logger.info(
                "Пользователь %s %s в админке",
                name,
                "заархивирован" if archived else "разархивирован",
            )
            return True
        if status == 404:
            raise AdminAPIError(f"Пользователь {name} не найден в админке", status)
        raise AdminAPIError(
            f"Не удалось {'заархивировать' if archived else 'разархивировать'} {name}: {status} {body}",
            status,
        )

    async def archive_user(self, name: str) -> bool:
        return await self.set_archived(name, True)

    async def unarchive_user(self, name: str) -> bool:
        return await self.set_archived(name, False)

    # --- Комбо-операции ---

    async def create_user_and_get_config(self, name: str) -> str:
        """Создать (или переиспользовать) пользователя и отдать VLESS-ссылку."""
        try:
            await self.create_user(name)
        except AdminAPIError:
            raise
        link = await self.get_vless_link(name)
        if not link:
            raise AdminAPIError(f"Пользователь {name} создан, но VLESS-ссылка не получена")
        return link

    async def get_or_create_user_config(self, name: str) -> str:
        link = await self.get_vless_link(name)
        if link:
            return link
        return await self.create_user_and_get_config(name)

    async def health_check(self) -> bool:
        try:
            status, _ = await self._request("GET", "/health")
            return status == 200
        except AdminAPIError:
            return False

    async def sync_state(self, name: str, archived: bool) -> bool:
        """Синхронизировать флаг в админке. Возвращает True, если состояние совпало.

        Нужно для идемпотентности: если бот упал между PATCH и commit, повторный
        вызов не должен считать рассинхрон за ошибку.
        """
        try:
            return await self.set_archived(name, archived)
        except AdminAPIError as e:
            if e.status == 404:
                return False
            raise


admin_api = AdminAPIClient()
