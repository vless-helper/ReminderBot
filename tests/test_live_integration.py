"""Жизненный цикл подписки на настоящем Postgres и настоящем cli-vless-manager.

Прогоняется только если заданы переменные окружения, поэтому в обычном
`pytest tests/` этот файл просто пропускается:

    TEST_LIVE_DB=postgresql+asyncpg://... \
    TEST_LIVE_API=http://vless-manager:8080 \
    pytest tests/test_live_integration.py -v

Здесь нет FakeAdminAPI: запросы идут в настоящий Go-API, а БД — в контейнер.
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from bot.config import utcnow
from bot.db import crud
from bot.db.base import create_engine_and_session
from bot.db.models import Base
from bot.handlers import admin as admin_handlers
from bot.handlers import user as user_handlers

LIVE_DB = os.getenv("TEST_LIVE_DB", "")
LIVE_API = os.getenv("TEST_LIVE_API", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not LIVE_DB or not LIVE_API, reason="нужны TEST_LIVE_DB и TEST_LIVE_API"),
]

ALICE_ID = 111111
BOB_ID = 222222
ADMIN_ID = 999


class _User:
    def __init__(self, uid: int):
        self.id = uid
        self.username = f"u{uid}"
        self.first_name = f"User{uid}"
        self.full_name = f"User {uid}"


class _Msg:
    """Минимальный Message: нужен только для вызовов хендлеров."""

    def __init__(self, bot, from_id: int, text: str = ""):
        self.bot = bot
        self.from_user = _User(from_id)
        self.chat = type("C", (), {"id": from_id})()
        self.text = text
        self.answers: list[str] = []
        self._reply_markup = None

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)
        return _Msg(self.bot, self.from_user.id, text)

    async def edit_text(self, text, reply_markup=None, **kwargs):
        return _Msg(self.bot, self.from_user.id, text)

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self._reply_markup = reply_markup


class _Cb:
    def __init__(self, bot, from_id: int, data: str):
        self.bot = bot
        self.from_user = _User(from_id)
        self.data = data
        self.message = _Msg(bot, from_id)
        self.answers: list[str] = []

    async def answer(self, text: str = "", **kwargs):
        self.answers.append(text)


class _FakeState:
    def __init__(self):
        self.data: dict = {}
        self.state_name = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state_name = state

    async def clear(self):
        self.data.clear()
        self.state_name = None


class _LiveBot:
    """Хватает бота только для get_db_session и отправки сообщений."""

    def __init__(self, session_maker, sent: list[tuple[int, str]]):
        self._sm = session_maker
        self.sent = sent

    def get_db_session(self):
        return self._sm()

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return _Msg(self, chat_id, text)


@pytest_asyncio.fixture
async def live():
    from bot.api.client import admin_api

    engine, session_maker = create_engine_and_session(LIVE_DB)
    async with engine.begin() as conn:
        await conn.execute(_TRUNCATE)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sent: list[tuple[int, str]] = []
    bot = _LiveBot(session_maker, sent)

    yield bot, session_maker, sent

    await admin_api.close()
    await engine.dispose()


async def test_full_subscription_lifecycle_against_real_stack(live):
    """Купил подписку → завёл конфиг → истёк → заблокирован → продлил → разблокирован."""
    from datetime import timedelta

    from bot.api.client import admin_api

    bot, session_maker, sent = live

    assert await admin_api.health_check() is True, "cli-vless-manager недоступен"

    alice = _Msg(bot, ALICE_ID, "/start")
    admin = _Cb(bot, ADMIN_ID, "noop")

    # 1. Регистрация и покупка подписки
    await user_handlers.cmd_start(alice, _FakeState())
    await user_handlers.buy_subscription(_Msg(bot, ALICE_ID, "📦 Купить подписку"))

    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        payment = (await crud.get_pending_payments(session, user.id))[0]
    assert payment.amount > 0

    await admin_handlers.confirm_payment(_Cb(bot, ADMIN_ID, f"ok:{payment.id}"))

    # 2. Конфиг должен появиться и в БД, и в админке
    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        configs = await crud.get_user_configs(session, user.id)
        sub = await crud.get_user_subscription(session, user.id)

    assert len(configs) == 1
    name = configs[0].config_name
    assert name == f"user_{ALICE_ID}_1"
    assert configs[0].vless_link and configs[0].vless_link.startswith("vless://")

    link = await admin_api.get_vless_link(name)
    assert link == configs[0].vless_link, "ссылка в БД должна совпадать с админкой"

    assert crud.is_active(sub)
    assert 28 <= (sub.next_payment - utcnow()).days <= 31

    # 3. Докупка второго конфига
    await user_handlers.my_configs(_Msg(bot, ALICE_ID, "📁 Мои конфиги"))
    await user_handlers.buy_extra_config(_Cb(bot, ALICE_ID, "cfgnew"))
    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        extra = [p for p in await crud.get_pending_payments(session, user.id) if p.type == "new_config"]
    assert len(extra) == 1, "должен быть ровно один платёж за доп. конфиг"
    await admin_handlers.confirm_payment(_Cb(bot, ADMIN_ID, f"ok:{extra[0].id}"))

    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        configs = await crud.get_user_configs(session, user.id)
    assert len(configs) == 2
    assert await admin_api.get_vless_link(f"user_{ALICE_ID}_2")

    # 4. Срок истёк — бот должен заблокировать оба конфига в админке
    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        sub = await crud.get_user_subscription(session, user.id)
        sub.next_payment = utcnow() - timedelta(hours=1)
        await session.commit()

    sent.clear()
    from bot.reminder import check_and_send_reminders

    await check_and_send_reminders(bot, session_maker)

    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        sub = await crud.get_user_subscription(session, user.id)
    assert sub.status == "expired"

    for number in (1, 2):
        archived = await admin_api.get_user(f"user_{ALICE_ID}_{number}")
        assert archived and archived.get("archived") is True, f"конфиг {number} не заблокирован"

    assert any("заморожена" in t.lower() for _, t in sent), "пользователь должен получить уведомление"

    # 5. Продление из замороженного состояния разблокирует обратно
    await user_handlers.extend_subscription(_Msg(bot, ALICE_ID, "🔄 Продлить подписку"))
    await user_handlers.extend_period_selected(_Cb(bot, ALICE_ID, "extend:1"))

    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        ext = [p for p in await crud.get_pending_payments(session, user.id) if p.type == "extend"]
    assert len(ext) == 1, "продление должно быть доступно и после заморозки"

    await admin_handlers.confirm_payment(_Cb(bot, ADMIN_ID, f"ok:{ext[0].id}"))

    async with session_maker() as session:
        user = await crud.get_user_by_telegram_id(session, ALICE_ID)
        sub = await crud.get_user_subscription(session, user.id)
    assert crud.is_active(sub), "подписка должна снова стать активной"

    for number in (1, 2):
        restored = await admin_api.get_user(f"user_{ALICE_ID}_{number}")
        assert restored and restored.get("archived") is False, f"конфиг {number} не разблокирован"

    # 6. Уборка
    for number in (1, 2):
        await admin_api.delete_user(f"user_{ALICE_ID}_{number}")
    async with session_maker() as session:
        await session.execute(_TRUNCATE)


async def test_second_user_is_independent(live):
    """Два плательщика не должны видеть конфиги и платежи друг друга."""
    from bot.api.client import admin_api

    bot, session_maker, _ = live

    for uid in (ALICE_ID, BOB_ID):
        await user_handlers.cmd_start(_Msg(bot, uid, "/start"), _FakeState())
        await user_handlers.buy_subscription(_Msg(bot, uid, "📦 Купить подписку"))
        async with session_maker() as session:
            user = await crud.get_user_by_telegram_id(session, uid)
            payment = (await crud.get_pending_payments(session, user.id))[0]
        await admin_handlers.confirm_payment(_Cb(bot, ADMIN_ID, f"ok:{payment.id}"))

    async with session_maker() as session:
        alice = await crud.get_user_by_telegram_id(session, ALICE_ID)
        bob = await crud.get_user_by_telegram_id(session, BOB_ID)
        alice_configs = await crud.get_user_configs(session, alice.id)
        bob_configs = await crud.get_user_configs(session, bob.id)

    assert len(alice_configs) == 1
    assert len(bob_configs) == 1
    assert alice_configs[0].config_name != bob_configs[0].config_name
    assert alice.id != bob.id

    assert await admin_api.get_vless_link(f"user_{ALICE_ID}_1")
    assert await admin_api.get_vless_link(f"user_{BOB_ID}_1")

    for uid in (ALICE_ID, BOB_ID):
        await admin_api.delete_user(f"user_{uid}_1")
    async with session_maker() as session:
        await session.execute(_TRUNCATE)


_TRUNCATE = text(
    "TRUNCATE reminder_log, payments, client_configs, subscriptions, users RESTART IDENTITY CASCADE"
)
