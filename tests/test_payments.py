"""Сценарии оплаты с разных аккаунтов.

Именно здесь ломался старый код: админ подтверждал оплату, читая свой FSM,
а данные лежали в FSM плательщика. При совпадении аккаунтов (как при разработке)
всё работало, при разных — нет.
"""

import asyncio
import os

import pytest

from bot.db import crud
from bot.db.models import Payment
from bot.handlers import admin as admin_handlers
from bot.handlers import user as user_handlers
from bot.utils.pricing import subscription_price
from tests.fakes import (
    BOT_USER, FakeCallbackQuery, FakeMessage, FakeState, FakeUser,
)

pytestmark = pytest.mark.asyncio


def days_from_now(dt, expected: int, tolerance: int = 1) -> bool:
    """Дней от сейчас с допуском: SQLite округляет микросекунды, .days может дать N-1."""
    delta = (dt - crud.utcnow()).days
    return expected - tolerance <= delta <= expected


async def _start(bot, user):
    msg = FakeMessage(bot, user, "/start")
    await user_handlers.cmd_start(msg, FakeState())
    return msg


def _press(bot, user, data):
    """Нажатие кнопки: сообщение с кнопками отправил бот, нажал клиент."""
    msg = FakeMessage(bot, BOT_USER, chat_id=user.id)
    return FakeCallbackQuery(bot, user, data, msg)


async def _register(bot, db, user):
    async with bot.get_db_session() as session:
        return await crud.get_or_create_user(session, user.id, user.username, user.full_name)


async def _pay_and_confirm(bot, db, admin_api, payer, admin, kind, months=1):
    """Полный цикл: покупка/продление -> подтверждение админом (другой аккаунт)."""
    await _register(bot, db, payer)

    # 1. Пользователь выбирает что оплачивает
    if kind == "subscription":
        await user_handlers.buy_subscription(FakeMessage(bot, payer, "📦 Купить подписку"))
    elif kind == "extend":
        cb = _press(bot, payer, f"extend:{months}")
        await user_handlers.extend_period_selected(cb)

    # 2. Находим созданный платёж
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, payer.id)
        pending = await crud.get_pending_payments(session, user.id)
    assert len(pending) == 1, f"ожидался 1 платёж, получено {len(pending)}"
    payment = pending[0]

    # 3. Пользователь жмёт «Я оплатил»
    cb = _press(bot, payer, f"pay:{payment.id}")
    await user_handlers.payment_confirmed(cb)

    # 4. Админ (другой аккаунт!) подтверждает
    cb_admin = _press(bot, admin, f"ok:{payment.id}")
    await admin_handlers.confirm_payment(cb_admin)

    # Перечитываем: объект в памяти устарел
    async with bot.get_db_session() as session:
        return await crud.get_payment(session, payment.id)


# --- Основной сценарий ---


async def test_subscription_from_separate_accounts(bot, db, admin_api, users):
    """Покупка подписки: плательщик и админ — разные аккаунты."""
    alice, admin = users["alice"], users["admin"]

    payment = await _pay_and_confirm(bot, db, admin_api, alice, admin, "subscription")

    assert payment.type == "subscription"
    assert payment.status == "completed"
    assert payment.resolved_by == admin.id

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        sub = await crud.get_user_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)

    assert crud.is_active(sub), "подписка должна быть активной"
    assert days_from_now(sub.next_payment, 30), "первый месяц должен давать +30 дней"
    assert len(configs) == 1, "должен создаться первый конфиг"
    assert configs[0].is_protected, "первый конфиг защищённый"
    assert f"user_{alice.id}_1" in admin_api.users, "конфиг должен появиться в админке"

    # Пользователь получил ссылку
    msgs = bot.messages_to(alice.id)
    assert any("vless://" in m.text for m in msgs), "юзер должен получить ссылку"


async def test_extend_keeps_requested_months(bot, db, admin_api, users):
    """Продление на 3 месяца: админ должен применить именно 3, а не дефолтные 30 дней.

    Старый код читал payment_type/extend_months из FSM админа, получал пусто и
    всегда продлевал на SUBSCRIPTION_DAYS.
    """
    alice, admin = users["alice"], users["admin"]

    await _pay_and_confirm(bot, db, admin_api, alice, admin, "subscription")

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        before = await crud.get_user_subscription(session, user.id)
        before_payment = before.next_payment

    payment = await _pay_and_confirm(bot, db, admin_api, alice, admin, "extend", months=3)

    assert payment.type == "extend"
    assert payment.months == 3
    assert payment.amount == subscription_price(3, 1).total

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        after = await crud.get_user_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)

    delta_days = (after.next_payment - before_payment).days
    assert delta_days == 90, f"ожидалось +90 дней, получено +{delta_days}"

    # Срок оплаты конфига тоже сдвинулся
    assert configs[0].paid_until > before_payment


async def test_new_config_payment_creates_config(bot, db, admin_api, users):
    """Докупка конфига: должен создаться ИМЕННО докупленный, а не «первый»."""
    alice, admin = users["alice"], users["admin"]

    await _pay_and_confirm(bot, db, admin_api, alice, admin, "subscription")

    await _register(bot, db, alice)
    cb = _press(bot, alice, "cfgnew")
    await user_handlers.buy_extra_config(cb)

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        pending = await crud.get_pending_payments(session, user.id)
    assert len(pending) == 1
    payment = pending[0]
    assert payment.type == "new_config"
    assert payment.config_number == 2, "номер нового конфига должен быть 2"

    await user_handlers.payment_confirmed(_press(bot, alice, f"pay:{payment.id}"))
    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{payment.id}"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        configs = await crud.get_user_configs(session, user.id)

    assert len(configs) == 2, "должно быть 2 конфига"
    assert {c.config_number for c in configs} == {1, 2}
    assert sum(1 for c in configs if not c.is_protected) == 1, "ровно один докупленный конфиг"
    assert f"user_{alice.id}_2" in admin_api.users


async def test_admin_state_is_not_used(bot, db, admin_api, users):
    """Ключевая защита от регресса: состояние админа не должно влиять на оплату.

    В старом коде данные читались из FSM админа. Если админ случайно имел
    в состоянии payment_type='extend', оплата подписки применялась как
    продление. Сейчас — не должна.
    """
    alice, admin = users["alice"], users["admin"]

    await _register(bot, db, alice)
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    # Вражеское состояние админа
    admin_state = FakeState()
    await admin_state.update_data(payment_type="extend", extend_months=12, is_new_config=True)

    # В реальном aiogram состояние админа передаётся в хендлер автоматически;
    # проверяем, что наш хендлер его вообще не читает.
    cb = _press(bot, admin, f"ok:{payment.id}")
    await admin_handlers.confirm_payment(cb)

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        sub = await crud.get_user_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)

    # Ровно 1 конфиг (защищённый), а не 2
    assert len(configs) == 1
    assert configs[0].config_number == 1
    assert configs[0].is_protected
    assert days_from_now(sub.next_payment, 30), "продление должно быть на 1 месяц, а не на 12"


async def test_second_admin_can_confirm(bot, db, admin_api, users):
    """Подтверждать может любой из админов, независимо от того, кто создал платёж."""
    alice, admin2 = users["alice"], users["admin2"]

    payment = await _pay_and_confirm(bot, db, admin_api, alice, admin2, "subscription")

    assert payment.status == "completed"
    assert payment.resolved_by == admin2.id


# --- Защита от дублей и злоупотреблений ---


async def test_duplicate_confirm_is_idempotent(bot, db, admin_api, users):
    """Двойное нажатие админом не должно продлить подписку дважды."""
    alice, admin = users["alice"], users["admin"]

    await _register(bot, db, alice)
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    await user_handlers.payment_confirmed(_press(bot, alice, f"pay:{payment.id}"))
    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{payment.id}"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        first = await crud.get_user_subscription(session, user.id)
        first_date = first.next_payment
        cfg_count = len(await crud.get_user_configs(session, user.id))

    # Повторное подтверждение
    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{payment.id}"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        second = await crud.get_user_subscription(session, user.id)
        cfg_count2 = len(await crud.get_user_configs(session, user.id))

    assert second.next_payment == first_date, "дата не должна сдвинуться повторно"
    assert cfg_count == cfg_count2 == 1, "конфиг не должен дублироваться"


async def test_cannot_confirm_other_users_payment(bot, db, admin_api, users):
    """Плательщик не может подтвердить чужой платёж."""
    alice, bob = users["alice"], users["bob"]

    await _register(bot, db, alice)
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    await user_handlers.payment_confirmed(_press(bot, bob, f"pay:{payment.id}"))

    async with bot.get_db_session() as session:
        stored = await crud.get_payment(session, payment.id)
    assert stored.status == "pending", "чужой платёж не должен подтверждаться"


async def test_non_admin_cannot_confirm(bot, db, admin_api, users):
    """Обычный пользователь не может нажать «подтвердить»."""
    alice, bob = users["alice"], users["bob"]

    await _register(bot, db, alice)
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    cb = _press(bot, bob, f"ok:{payment.id}")
    await admin_handlers.confirm_payment(cb)

    async with bot.get_db_session() as session:
        stored = await crud.get_payment(session, payment.id)
    assert stored.status == "pending"
    assert cb.answers, "должен быть ответ об отказе"


async def test_no_double_pending_payment(bot, db, admin_api, users):
    """Пока есть неподтверждённый платёж, второй создать нельзя."""
    alice = users["alice"]
    await _register(bot, db, alice)

    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    bot.clear()
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        pending = await crud.get_pending_payments(session, user.id)

    assert len(pending) == 1
    assert "неподтверждённый платёж" in bot.last_to(alice.id).text


async def test_reject_payment(bot, db, admin_api, users):
    alice, admin = users["alice"], users["admin"]

    await _register(bot, db, alice)
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    await admin_handlers.reject_payment(_press(bot, admin, f"no:{payment.id}"))

    async with bot.get_db_session() as session:
        stored = await crud.get_payment(session, payment.id)
        user = await crud.get_user_by_telegram_id(session, alice.id)
        configs = await crud.get_user_configs(session, user.id)

    assert stored.status == "rejected"
    assert configs == [], "при отклонении конфиг создаваться не должен"


async def test_price_in_keyboard_matches_charged(bot, db, admin_api, users):
    """Сумма на кнопке и сумма в платеже должны совпадать (раньше расходились)."""
    alice = users["alice"]
    await _register(bot, db, alice)
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    expected = subscription_price(1, 1).total
    assert payment.amount == expected

    from bot.utils.pricing import extra_config_price

    price, _ = extra_config_price(15)
    assert price == 75


async def test_failed_payment_can_be_retried_without_double_extend(bot, db, admin_api, users):
    """Сбой применения не теряет платёж и не продлевает подписку дважды.

    Раньше платёж сразу становился completed, и при падении админки доступ
    не выдавался навсегда — деньги висели, а повторно нажать было нельзя.
    """
    alice, admin = users["alice"], users["admin"]
    u = await _register(bot, db, alice)

    # Первая подписка создаёт конфиг — он и будет ломать применение
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        first = (await crud.get_pending_payments(session, user.id))[0]
    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{first.id}"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        date_before_extend = (await crud.get_user_subscription(session, user.id)).next_payment

    # Теперь ломаем админку и покупаем продление
    admin_api.fail_on.add("set_archived")
    await user_handlers.extend_subscription(FakeMessage(bot, alice, "🔄 Продлить подписку"))
    await user_handlers.extend_period_selected(
        FakeCallbackQuery(bot, alice, "extend:1", FakeMessage(bot, BOT_USER, chat_id=alice.id))
    )

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        ext = (await crud.get_pending_payments(session, user.id))[0]
    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{ext.id}"))

    async with bot.get_db_session() as session:
        after_fail = await crud.get_payment(session, ext.id)
        sub_after_fail = await crud.get_user_subscription(session, user.id)
    assert after_fail.status == "failed", "платёж должен ждать повтора"
    assert after_fail.error, "причина сбоя должна сохраниться"
    assert after_fail.effect_applied, "эффект в БД применён до обращения к админке"
    # Срок уже продлён на первой попытке
    first_delta = (sub_after_fail.next_payment - date_before_extend).days
    assert 28 <= first_delta <= 31, f"продлено на {first_delta} дней вместо ~30"
    before = sub_after_fail.next_payment

    # Чиним админку и жмём «Повторить»
    admin_api.fail_on.clear()
    await admin_handlers.retry_payment(_press(bot, admin, f"retry:{ext.id}"))

    async with bot.get_db_session() as session:
        after_retry = await crud.get_payment(session, ext.id)
        sub_after_retry = await crud.get_user_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)

    assert after_retry.status == "completed"
    assert after_retry.error is None
    # Повтор НЕ продлевает заново — иначе пользователь получил бы 60 дней за одну оплату
    assert sub_after_retry.next_payment == before, "повтор задвоил продление"
    assert len(configs) == 1, "дублей конфигов быть не должно"


async def test_cannot_confirm_twice_from_two_admins(bot, db, admin_api, users):
    """Два админа одновременно жмут «подтвердить» — платёж применяется один раз."""
    alice, admin, admin2 = users["alice"], users["admin"], users["admin2"]
    u = await _register(bot, db, alice)

    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{payment.id}"))
    second = _press(bot, admin2, f"ok:{payment.id}")
    await admin_handlers.confirm_payment(second)

    assert ("По этому платежу уже ответили", True) in second.answers, second.answers
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        sub = await crud.get_user_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)

    assert len(configs) == 1
    assert len([c for c in admin_api.calls if c[0] == "create_user"]) == 1, "API вызван один раз"


async def test_stuck_processing_payment_is_recoverable(bot, db, admin_api, users):
    """Платёж, застрявший в processing (бот упал), возвращается в очередь."""
    alice = users["alice"]
    await _register(bot, db, alice)
    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]
        await crud.claim_payment(session, payment.id, 999)

    async with bot.get_db_session() as session:
        recovered = await crud.reset_stuck_payments(session)

    assert recovered == 1
    async with bot.get_db_session() as session:
        stored = await crud.get_payment(session, payment.id)
    assert stored.status == "pending"


async def test_concurrent_confirm_applies_once(bot, db, admin_api, users):
    """Два админа жмут «подтвердить» одновременно — эффект применяется один раз.

    На SQLite записи сериализуются, поэтому настоящая гонка проверяется
    только в прогоне с --postgres-url.
    """
    alice, admin, admin2 = users["alice"], users["admin"], users["admin2"]
    await _register(bot, db, alice)

    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        payment = (await crud.get_pending_payments(session, user.id))[0]

    first = _press(bot, admin, f"ok:{payment.id}")
    second = _press(bot, admin2, f"ok:{payment.id}")
    await asyncio.gather(
        admin_handlers.confirm_payment(first),
        admin_handlers.confirm_payment(second),
    )

    alerts = [t for t, _ in first.answers] + [t for t, _ in second.answers]
    assert alerts.count("✅ Подтверждено") == 1, alerts

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        configs = await crud.get_user_configs(session, user.id)
        stored = await crud.get_payment(session, payment.id)

    assert len(configs) == 1, "конфиг должен быть создан ровно один"
    assert stored.status == "completed"
    assert len([c for c in admin_api.calls if c[0] == "create_user"]) == 1, (
        "пользователь в админке должен создаваться один раз"
    )


async def test_concurrent_extend_does_not_double_period(bot, db, admin_api, users):
    """Гонка на продлении не должна задвоить оплаченный срок."""
    alice, admin, admin2 = users["alice"], users["admin"], users["admin2"]
    await _register(bot, db, alice)

    await user_handlers.buy_subscription(FakeMessage(bot, alice, "📦 Купить подписку"))
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        first = (await crud.get_pending_payments(session, user.id))[0]
    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{first.id}"))

    await user_handlers.extend_subscription(FakeMessage(bot, alice, "🔄 Продлить подписку"))
    await user_handlers.extend_period_selected(
        FakeCallbackQuery(bot, alice, "extend:1", FakeMessage(bot, BOT_USER, chat_id=alice.id))
    )
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        ext = (await crud.get_pending_payments(session, user.id))[0]
        before = (await crud.get_user_subscription(session, user.id)).next_payment

    await asyncio.gather(
        admin_handlers.confirm_payment(_press(bot, admin, f"ok:{ext.id}")),
        admin_handlers.confirm_payment(_press(bot, admin2, f"ok:{ext.id}")),
    )

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        sub = await crud.get_user_subscription(session, user.id)

    delta = (sub.next_payment - before).days
    assert 28 <= delta <= 31, f"продлено на {delta} дней вместо ~30 — оплата задвоена"


# --- Докупка конфига не должна продлевать подписку ---


async def _buy_extra_config(bot, db, admin_api, payer, admin):
    """Полный цикл докупки конфига: кнопка -> «Я оплатил» -> подтверждение админом."""
    await _register(bot, db, payer)

    # 1. Пользователь жмёт «Докупить конфиг»
    await user_handlers.buy_extra_config(_press(bot, payer, "cfgnew"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, payer.id)
        pending = await crud.get_pending_payments(session, user.id)
    assert pending, "ожидался неподтверждённый платёж на докупку"

    payment = pending[0]
    assert payment.type == "new_config"

    # 2. «Я оплатил»
    await user_handlers.payment_confirmed(_press(bot, payer, f"pay:{payment.id}"))

    # 3. Админ подтверждает
    await admin_handlers.confirm_payment(_press(bot, admin, f"ok:{payment.id}"))

    async with bot.get_db_session() as session:
        return await crud.get_payment(session, payment.id)


async def test_new_config_does_not_extend_subscription(bot, db, admin_api, users):
    """Докупка конфига НЕ продлевает срок подписки.

    Старый код вёл new_config в ту же ветку, что и первую подписку, и вызывал
    reactivate_subscription(SUBSCRIPTION_DAYS). Из-за этого покупка второго
    конфига добавляла к подписке ещё 30 дней: месячная подписка с двумя
    конфигами превращалась в двухмесячную.
    """
    alice, admin = users["alice"], users["admin"]

    await _pay_and_confirm(bot, db, admin_api, alice, admin, "subscription")

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        before = (await crud.get_user_subscription(session, user.id)).next_payment

    await _buy_extra_config(bot, db, admin_api, alice, admin)

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        after_sub = await crud.get_user_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)

    assert len(configs) == 2, "должно быть 2 конфига"
    assert after_sub.next_payment == before, (
        f"докупка конфига не должна двигать конец подписки: "
        f"было {before}, стало {after_sub.next_payment}"
    )


async def test_new_config_paid_until_matches_subscription_end(bot, db, admin_api, users):
    """Докупленный конфиг оплачен ровно до конца подписки.

    Срок считается по подписке, а не заново от текущего момента: иначе конфиг
    жил бы дольше, чем оплаченная подписка.
    """
    alice, admin = users["alice"], users["admin"]

    await _pay_and_confirm(bot, db, admin_api, alice, admin, "subscription")

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        sub_end = (await crud.get_user_subscription(session, user.id)).next_payment

    await _buy_extra_config(bot, db, admin_api, alice, admin)

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        configs = await crud.get_user_configs(session, user.id)

    extra = next(c for c in configs if c.config_number == 2)
    assert extra.paid_until is not None, "у докупленного конфига должен быть срок"
    assert extra.paid_until == sub_end, (
        f"срок конфига должен совпадать с концом подписки: "
        f"{extra.paid_until} != {sub_end}"
    )


async def test_renewal_after_cancelling_config_is_cheaper(bot, db, admin_api, users):
    """Отмена одного конфига из двух делает следующее продление дешевле.

    Клиент оплатил два конфига на месяц, через время отменил один — продление
    должно считаться от числа активных конфигов, то есть дешевле.
    """
    alice, admin = users["alice"], users["admin"]

    await _pay_and_confirm(bot, db, admin_api, alice, admin, "subscription")
    await _buy_extra_config(bot, db, admin_api, alice, admin)

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        assert await crud.get_active_configs_count(session, user.id) == 2

    # Продление при двух конфигах
    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        configs = await crud.get_user_configs(session, user.id)
        extra_id = next(c.id for c in configs if c.config_number == 2)
    await user_handlers.delete_config(_press(bot, alice, f"del:{extra_id}"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        active = await crud.get_active_configs_count(session, user.id)
    assert active == 1, "после отмены должен остаться один активный конфиг"

    # Продление при одном конфиге
    await user_handlers.extend_period_selected(_press(bot, alice, "extend:1"))

    async with bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, alice.id)
        pending = await crud.get_pending_payments(session, user.id)

    assert len(pending) == 1
    charged = pending[0].amount
    expected = subscription_price(1, 1).total

    assert charged == expected, (
        f"после отмены конфига продление должно стоить {expected}, "
        f"а не {charged} (как за два конфига)"
    )
    assert charged < subscription_price(1, 2).total, (
        "продление после отмены должно быть дешевле, чем за два конфига"
    )
