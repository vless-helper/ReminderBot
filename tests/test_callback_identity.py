"""Регресс: identity нажавшего inline-кнопку.

У сообщения с инлайн-кнопками from_user — это БОТ: сообщение отправил он,
клиент лишь нажал. Если хендлер берёт callback.message.from_user, он ищет
в БД telegram_id бота и не находит его — клиент получает
«Пользователь не найден. Отправьте /start».

Раньше фейки в tests/fakes.py подставляли клиента и туда, и туда, и баг
проходил весь набор тестов незамеченным. Теперь FakeCallbackQuery требует
сообщение бота, а ниже стоит проверка, что сама фикстура не врёт.
"""

import pytest
from bot.db import crud
from bot.handlers import user as user_handlers
from tests.fakes import (
    BOT_USER,
    FakeCallbackQuery,
    FakeMessage,
    FakeState,
    FakeUser,
)

NOT_FOUND = "Пользователь не найден"


def press(bot, who: FakeUser, data: str) -> FakeCallbackQuery:
    """Нажатие кнопки на сообщении, которое отправил бот."""
    return FakeCallbackQuery(bot, who, data,
                             FakeMessage(bot, BOT_USER, chat_id=who.id))


def replies(bot, who: FakeUser) -> str:
    return "\n".join(m.text for m in bot.messages_to(who.id))


async def _client_with_config(bot, who: FakeUser) -> int:
    await user_handlers.cmd_start(FakeMessage(bot, who, "/start"), FakeState())
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, who.id)
        await crud.create_client_config(session, user_id=user.id, config_number=1,
                                        config_name=f"user_{who.id}_1",
                                        vless_link="", is_protected=True)
        await crud.extend_subscription_months(session, user.id, 1)
        await session.commit()
        return user.id


def _buttons(sent):
    out = []
    for row in (sent.reply_markup.inline_keyboard or []):
        for btn in row:
            out.append((btn.text, btn.callback_data))
    return out


def test_fakes_mirror_telegram():
    """Фейк колбэка обязан повторять реальное устройство Telegram."""
    who = FakeUser(id=111111, username="alice")
    bot = object()
    cb = FakeCallbackQuery(bot, who, "extend:1",
                           FakeMessage(bot, BOT_USER, chat_id=who.id))

    assert cb.from_user.id == who.id, "нажавший — клиент"
    assert cb.message.from_user.id == BOT_USER.id, \
        "автор сообщения с кнопками — бот, а не клиент"
    assert cb.message.chat.id == who.id, "переписка идёт в чат клиента"


@pytest.mark.asyncio
async def test_extend_button_finds_pressing_user(bot, users):
    alice = users["alice"]
    await _client_with_config(bot, alice)
    bot.clear()

    await user_handlers.extend_period_selected(press(bot, alice, "extend:1"))

    assert NOT_FOUND not in replies(bot, alice), replies(bot, alice)
    assert "Продление" in replies(bot, alice), replies(bot, alice)

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        pending = await crud.get_pending_payments(session, user.id)
    assert [p for p in pending if p.type == "extend"], "платёж на продление не создан"


@pytest.mark.asyncio
async def test_cfgnew_button_finds_pressing_user(bot, users):
    alice = users["alice"]
    await _client_with_config(bot, alice)
    bot.clear()

    await user_handlers.buy_extra_config(press(bot, alice, "cfgnew"))

    assert NOT_FOUND not in replies(bot, alice), replies(bot, alice)
    assert "Докупка" in replies(bot, alice), replies(bot, alice)

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        pending = await crud.get_pending_payments(session, user.id)
    assert [p for p in pending if p.type == "new_config"], "платёж на докупку не создан"


@pytest.mark.asyncio
async def test_question_button_opens_question(bot, users, admin_api):
    """Регресс на инвертированный StateFilter: кнопка молчала, когда
    состояния ещё не было — то есть почти всегда."""
    alice = users["alice"]
    await user_handlers.cmd_start(FakeMessage(bot, alice, "/start"), FakeState())
    bot.clear()

    await user_handlers.ask_question(FakeMessage(bot, alice, "❓ Задать вопрос"),
                                    FakeState())

    assert "вопрос" in replies(bot, alice).lower(), replies(bot, alice)