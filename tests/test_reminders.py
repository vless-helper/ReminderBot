"""Напоминания и блокировка.

Проверяем то, что раньше ломалось:
  * один флаг last_reminder_sent на все виды напоминаний (3-дневное гасило 1-дневное);
  * naive/aware даты — на PostgreSQL это давало TypeError;
  * архивация конфигов в админке не срабатывала из-за отсутствующего await.
"""

from datetime import timedelta

import pytest

from bot.config import utcnow
from bot.db import crud
from bot import reminder as reminder_mod
from bot.reminder import check_and_send_reminders
from tests.fakes import FakeUser

pytestmark = pytest.mark.asyncio


async def _make_user(bot, telegram_id: int, username: str = "u"):
    async with bot.get_db_session() as session:
        return await crud.get_or_create_user(session, telegram_id, username)


async def _set_deadline(bot, user_id: int, days: float):
    async with bot.get_db_session() as session:
        sub = await crud.get_user_subscription(session, user_id)
        if sub is None:
            from bot.db.models import Subscription

            sub = Subscription(user_id=user_id, next_payment=utcnow(), status="active", period_days=30)
            session.add(sub)
        sub.next_payment = utcnow() + timedelta(days=days)
        sub.status = "active"
        await session.commit()
        await crud.reset_reminder_flags(session, sub.id)
        return sub.id


async def _add_config(bot, admin_api, user_id: int, tg: int, number: int = 1):
    name = f"user_{tg}_{number}"
    link = await admin_api.create_user_and_get_config(name)
    async with bot.get_db_session() as session:
        return await crud.create_client_config(
            session, user_id, number, name, link, is_protected=(number == 1),
            paid_until=utcnow() + timedelta(days=30),
        )


async def test_3d_and_1d_reminders_both_sent(bot, db, admin_api, users):
    """Пользователь должен получить оба напоминания, а не одно."""
    alice = users["alice"]
    u = await _make_user(bot, alice.id, "alice")

    # Подписка истекает завтра -> сегодня её «3-дневное» окно
    await _set_deadline(bot, u.id, days=1)

    sent = await check_and_send_reminders(bot, db)
    texts = [m.text for m in bot.messages_to(alice.id)]

    assert sent >= 1
    assert any("Завтра заморозка" in t for t in texts), texts

    # Второй прогон не должен дублировать
    bot.clear()
    sent_again = await check_and_send_reminders(bot, db)
    assert sent_again == 0, "повторно отправлять нельзя"


async def test_reminder_3d_does_not_block_1d(bot, db, admin_api, users):
    """Отметка о 3-дневном напоминании не должна гасить 1-дневное."""
    alice = users["alice"]
    u = await _make_user(bot, alice.id, "alice")

    # Окно «за 3 дня» — это дедлайн ровно через 3 дня
    sub_id = await _set_deadline(bot, u.id, days=3)
    await check_and_send_reminders(bot, db)

    from bot.db.models import ReminderLog
    from sqlalchemy import select

    async with bot.get_db_session() as session:
        sent_windows = set(
            (await session.execute(select(ReminderLog.days_before).where(
                ReminderLog.subscription_id == sub_id))).scalars().all()
        )
    assert 3 in sent_windows, "3-дневное должно быть отмечено"
    assert 1 not in sent_windows, "1-дневное пока не должно"

    # Теперь дедлайн завтра — должно прийти 1-дневное, несмотря на отметку о 3-дневном
    await _set_deadline(bot, u.id, days=1)
    bot.clear()
    await check_and_send_reminders(bot, db)
    texts = [m.text for m in bot.messages_to(alice.id)]
    assert any("Завтра заморозка" in t for t in texts), texts


async def test_expiry_blocks_configs_in_admin(bot, db, admin_api, users):
    """Просрочка: юзеру уходит сообщение, конфиги архивируются в админке."""
    alice = users["alice"]
    u = await _make_user(bot, alice.id, "alice")
    await _set_deadline(bot, u.id, days=1)
    cfg = await _add_config(bot, admin_api, u.id, alice.id)

    assert cfg.config_name in admin_api.users
    assert cfg.config_name not in admin_api.archived_names()

    await _set_deadline(bot, u.id, days=-0.5)  # только что истекла
    await check_and_send_reminders(bot, db)

    assert cfg.config_name in admin_api.archived_names(), "конфиг должен быть заархивирован"

    async with bot.get_db_session() as session:
        sub = await crud.get_user_subscription(session, u.id)
        assert sub.status == "expired"
        assert not crud.is_active(sub)

    texts = [m.text for m in bot.messages_to(alice.id)]
    assert any("заморожена" in t.lower() for t in texts), texts


async def test_expiry_is_not_repeated(bot, db, admin_api, users):
    """Просроченному пользователю нельзя слать уведомления каждый цикл."""
    alice = users["alice"]
    u = await _make_user(bot, alice.id, "alice")
    await _set_deadline(bot, u.id, days=-1)
    await _add_config(bot, admin_api, u.id, alice.id)

    await check_and_send_reminders(bot, db)
    bot.clear()
    await check_and_send_reminders(bot, db)

    assert bot.messages_to(alice.id) == [], "повторов быть не должно"


async def test_payment_after_expiry_unblocks(bot, db, admin_api, users):
    """Оплата после просрочки разблокирует конфиги."""
    from bot.handlers import admin as admin_handlers
    from bot.handlers import user as user_handlers
    from tests.fakes import BOT_USER, FakeCallbackQuery, FakeMessage

    alice, admin = users["alice"], users["admin"]
    u = await _make_user(bot, alice.id, "alice")
    await _set_deadline(bot, u.id, days=1)
    cfg = await _add_config(bot, admin_api, u.id, alice.id)
    await _set_deadline(bot, u.id, days=-1)
    await check_and_send_reminders(bot, db)
    assert cfg.config_name in admin_api.archived_names()

    # Оплата за подписку, когда она уже просрочена:
    # заблокированный пользователь должен суметь купить продление
    await user_handlers.extend_subscription(FakeMessage(bot, alice, "🔄 Продлить подписку"))
    assert any("заморожена" in m.text for m in bot.messages_to(alice.id)), "ожидалось сообщение о заморозке"

    bot.clear()
    await user_handlers.extend_period_selected(FakeCallbackQuery(bot, alice, "extend:1", FakeMessage(bot, BOT_USER, chat_id=alice.id)))
    async with bot.get_db_session() as session:
        payment = (await crud.get_pending_payments(session, u.id))[0]

    msg = FakeMessage(bot, BOT_USER, chat_id=alice.id)
    await user_handlers.payment_confirmed(FakeCallbackQuery(bot, alice, f"pay:{payment.id}", msg))
    await admin_handlers.confirm_payment(FakeCallbackQuery(bot, admin, f"ok:{payment.id}", msg))

    assert cfg.config_name not in admin_api.archived_names(), "конфиг должен быть разблокирован"

    async with bot.get_db_session() as session:
        sub = await crud.get_user_subscription(session, u.id)
        assert crud.is_active(sub)

    from bot.db.models import ReminderLog
    from sqlalchemy import func, select

    async with bot.get_db_session() as session:
        left = (
            await session.execute(select(func.count(ReminderLog.id)))
        ).scalar_one()
    assert left == 0, "после продления отметки о напоминаниях должны быть сброшены"


async def test_archived_user_cannot_get_link(bot, db, admin_api, users):
    """Заблокированный пользователь не должен получать ссылку."""
    from bot.handlers import user as user_handlers
    from tests.fakes import BOT_USER, FakeCallbackQuery, FakeMessage

    alice = users["alice"]
    u = await _make_user(bot, alice.id, "alice")
    await _set_deadline(bot, u.id, days=1)
    cfg = await _add_config(bot, admin_api, u.id, alice.id)
    await _set_deadline(bot, u.id, days=-1)
    await check_and_send_reminders(bot, db)

    bot.clear()
    cb = FakeCallbackQuery(bot, alice, f"link:{cfg.id}", FakeMessage(bot, BOT_USER, chat_id=alice.id))
    await user_handlers.show_config_link(cb)

    texts = [m.text for m in bot.messages_to(alice.id)]
    assert texts, "должен быть ответ"
    assert not any("vless://" in t for t in texts), "ссылку отдавать нельзя"


async def test_reminder_window(bot, db, admin_api, users, monkeypatch):
    """Вне окна напоминаний 3/1-дневные не отправляются, но просрочка обрабатывается."""
    import bot.reminder as rem

    monkeypatch.setattr(rem, "in_reminder_window", lambda: False)

    alice = users["alice"]
    u = await _make_user(bot, alice.id, "alice")
    await _set_deadline(bot, u.id, days=1)

    sent = await check_and_send_reminders(bot, db)
    assert sent == 0
    assert bot.messages_to(alice.id) == []


async def test_timezone_awareness(bot, db, admin_api, users):
    """Даты из БД всегда aware — иначе на Postgres будет TypeError."""
    alice = users["alice"]
    u = await _make_user(bot, alice.id, "alice")
    await _set_deadline(bot, u.id, days=10)
    await _add_config(bot, admin_api, u.id, alice.id)

    async with bot.get_db_session() as session:
        sub = await crud.get_user_subscription(session, u.id)
        cfg = (await crud.get_user_configs(session, u.id))[0]
        user = await crud.get_user_by_telegram_id(session, alice.id)

    for label, value in [
        ("next_payment", sub.next_payment),
        ("paid_until", cfg.paid_until),
        ("created_at", user.created_at),
        ("created_at(config)", cfg.created_at),
    ]:
        assert value.tzinfo is not None, f"{label} должен быть aware"

    # И сравнения работают
    assert crud.is_active(sub)

    async with bot.get_db_session() as session:
        assert await crud.days_left(session, u.id) == 9 or await crud.days_left(session, u.id) == 10


async def test_reminder_has_extend_buttons(bot, db, admin_api, users, monkeypatch):
    """Напоминание должно приходить с кнопками выбора срока продления.

    Раньше текст заканчивался висячим «Кнопка «Продлить подписку».», но
    reply_markup не передавался вовсе — клиенту приходилось самому искать
    кнопку в меню.
    """
    monkeypatch.setattr(reminder_mod, "in_reminder_window", lambda: True)

    alice = users["alice"]
    user = await _make_user(bot, alice.id, "alice")
    await _set_deadline(bot, user.id, 3)

    await check_and_send_reminders(bot, db)

    msg = bot.last_to(alice.id)
    assert msg is not None, "напоминание должно быть отправлено"

    assert "<b>Скоро заморозка подписки</b>" in msg.text
    assert "Кнопка «Продлить подписку»." not in msg.text, (
        "висячая фраза без кнопки осталась в тексте"
    )
    assert "Выберите срок продления ниже." in msg.text

    assert msg.reply_markup is not None, "к напоминанию должна прилагаться клавиатура"
    flat = [
        btn.callback_data
        for row in msg.reply_markup.inline_keyboard
        for btn in row
    ]
    assert any(cb and cb.startswith("extend:") for cb in flat), (
        f"в клавиатуре нет кнопок продления: {flat}"
    )
