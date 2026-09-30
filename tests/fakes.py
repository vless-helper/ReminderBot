"""Фейковые объекты aiogram для тестов без Telegram.

Хендлеры используют утиный тайпинг, поэтому достаточно простых классов.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from bot.db.base import create_engine_and_session


@dataclass
class FakeUser:
    id: int
    username: Optional[str] = None
    first_name: str = ""
    last_name: str = ""
    full_name: str = "Test User"

    def __post_init__(self):
        if not self.full_name:
            self.full_name = f"{self.first_name} {self.last_name}".strip() or "Test"


@dataclass
class Sent:
    chat_id: int
    text: str
    reply_markup: Any = None
    parse_mode: Optional[str] = None


class FakeBot:
    """Пишет все исходящие сообщения в список — их можно проверять."""

    def __init__(self, session_maker):
        self.session_maker = session_maker
        self.sent: list[Sent] = []
        self.session_maker_ref = session_maker

    def get_db_session(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _session():
            async with self.session_maker() as session:
                try:
                    yield session
                finally:
                    await session.close()

        return _session()

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **kwargs):
        self.sent.append(Sent(chat_id, text, reply_markup, parse_mode))
        return Sent(chat_id, text)

    def messages_to(self, chat_id: int) -> list[Sent]:
        return [m for m in self.sent if m.chat_id == chat_id]

    def last_to(self, chat_id: int) -> Optional[Sent]:
        msgs = self.messages_to(chat_id)
        return msgs[-1] if msgs else None

    def clear(self):
        self.sent.clear()


class FakeMessage:
    def __init__(self, bot: FakeBot, from_user: FakeUser, text: str = "", message_id: int = 0):
        self.bot = bot
        self.from_user = from_user
        self.text = text
        self.message_id = message_id
        self.chat = type("Chat", (), {"id": from_user.id, "type": "private"})()
        self.date = None
        self.reply_markup = None
        self.deleted = False
        self.edits: list[str] = []

    async def answer(self, text, reply_markup=None, parse_mode=None, **kwargs):
        self.reply_markup = reply_markup
        return await self.bot.send_message(self.from_user.id, text, reply_markup, parse_mode)

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.reply_markup = reply_markup

    async def edit_text(self, text, reply_markup=None, parse_mode=None, **kwargs):
        self.text = text
        self.edits.append(text)
        return await self.bot.send_message(self.from_user.id, text, reply_markup, parse_mode)

    async def delete(self):
        self.deleted = True
        return True

    # хелпер для тестов
    def buttons(self) -> list[tuple[str, str]]:
        """Список (текст, callback_data) кнопок последней отправленной разметки."""
        out = []
        for row in (self.reply_markup.inline_keyboard or []):
            for btn in row:
                out.append((btn.text, btn.callback_data))
        return out


class FakeCallbackQuery:
    def __init__(self, bot: FakeBot, from_user: FakeUser, data: str, message: FakeMessage):
        self.bot = bot
        self.from_user = from_user
        self.data = data
        self.message = message
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False, **kwargs):
        self.answers.append((text, show_alert))

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.message._reply_markup = reply_markup

    async def edit_text(self, text, reply_markup=None, parse_mode=None, **kwargs):
        self.message.text = text
        return await self.bot.send_message(self.from_user.id, text, reply_markup, parse_mode)


class FakeState:
    """Минимальный FSMContext. В тестах нам важно, что состояние НЕ переносится
    между аккаунтами — как и было в старом коде."""

    def __init__(self):
        self.data: dict = {}
        self.state_name = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, name):
        self.state_name = name

    async def get_state(self):
        return self.state_name

    async def clear(self):
        self.data = {}
        self.state_name = None


class FakeAdminAPI:
    """Заглушка cli-vless-manager: хранит пользователей, считает архивные."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.fail_on: set[str] = set()

    def _maybe_fail(self, op: str, name: str):
        if op in self.fail_on:
            from bot.api.client import AdminAPIError

            raise AdminAPIError(f"имитация сбоя: {op} {name}", status=500)

    async def create_user(self, name):
        self.calls.append(("create_user", name))
        self._maybe_fail("create_user", name)
        created = name not in self.users
        self.users.setdefault(name, {"name": name, "archived": False, "vless": self._link(name)})
        return True, "created" if created else "already_exists"

    async def get_vless_link(self, name):
        self.calls.append(("get_vless_link", name))
        self._maybe_fail("get_vless_link", name)
        user = self.users.get(name)
        return user["vless"] if user else None

    async def create_user_and_get_config(self, name):
        await self.create_user(name)
        return await self.get_vless_link(name)

    async def get_or_create_user_config(self, name):
        link = await self.get_vless_link(name)
        return link or await self.create_user_and_get_config(name)

    async def delete_user(self, name):
        self.calls.append(("delete_user", name))
        self._maybe_fail("delete_user", name)
        self.users.pop(name, None)
        return True

    async def set_archived(self, name, archived):
        self.calls.append(("set_archived", name, archived))
        self._maybe_fail("set_archived", name)
        if name not in self.users:
            from bot.api.client import AdminAPIError

            raise AdminAPIError(f"Пользователь {name} не найден в админке", status=404)
        self.users[name]["archived"] = archived
        return True

    async def archive_user(self, name):
        return await self.set_archived(name, True)

    async def unarchive_user(self, name):
        return await self.set_archived(name, False)

    async def get_all_users(self):
        return list(self.users.values())

    async def health_check(self):
        return True

    @staticmethod
    def _link(name: str) -> str:
        return f"vless://uuid-{name}@ads.x5.ru:443?type=ws&sni=ads.x5.ru#{name}"

    def archived_names(self) -> set[str]:
        return {n for n, u in self.users.items() if u["archived"]}
