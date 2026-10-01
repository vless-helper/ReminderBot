"""Пользовательские хендлеры.

Ключевое отличие от старой версии: платёж создаётся в БД сразу при нажатии
кнопки оплаты, и его id уходит в callback_data. Админ подтверждает конкретный
платёж, а не пытается достать контекст из своего FSM.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import config, utcnow
from bot.db import crud
from bot.keyboards.keyboards import (
    get_admin_payment_keyboard,
    get_config_actions_keyboard,
    get_configs_keyboard,
    get_extend_keyboard,
    get_main_keyboard,
    get_payment_keyboard,
    get_question_keyboard,
    render_price_breakdown,
)
from bot.utils.helpers import format_date, format_months, format_price
from bot.utils.pricing import extra_config_price, monthly_price, subscription_price

logger = logging.getLogger(__name__)
router = Router()

MAIN_MENU_BUTTONS = (
    "📦 Купить подписку",
    "🔄 Продлить подписку",
    "❓ Задать вопрос",
    "ℹ️ Моя подписка",
    "📱 Мои конфиги",
)


class QuestionState(StatesGroup):
    waiting_for_question = State()


async def _require_user(telegram_id: int, reply_to: Message, session):
    """Достать пользователя по telegram_id. None -> отправлен ответ в /start.

    telegram_id передаётся отдельно и намеренно первым: у inline-кнопок
    нельзя брать callback.message.from_user — там лежит БОТ, потому что
    сообщение с клавиатурой отправил он. Нажавшего лежит в callback.from_user.
    Из-за путаницы клиент получал «пользователь не найден» на кнопках
    продления и докупки конфига.

    Возвращаем объект User, потому что все crud-функции ждут внутренний
    user_id, а не telegram_id.
    """
    user = await crud.get_user_by_telegram_id(session, telegram_id)
    if not user:
        await reply_to.answer("❌ Пользователь не найден. Отправьте /start")
    return user


# --- Меню ---


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    async with message.bot.get_db_session() as session:
        user = await crud.get_or_create_user(
            session,
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
        )
        active = await crud.check_subscription_status(session, user.id)
        configs_count = await crud.get_active_configs_count(session, user.id)

    text = (
        "👋 <b>Amnesia VPN</b>\n\n"
        f"💰 Тариф: {format_price(config.BASE_PRICE)} за конфиг в месяц\n\n"
        "Что можно сделать:\n"
        "• <b>Купить подписку</b> — первый конфиг сразу после оплаты\n"
        "• <b>Продлить подписку</b> — скидка за срок\n"
        "• <b>Мои конфиги</b> — ссылки, докупка, удаление\n"
        "• <b>Задать вопрос</b> — ответит администратор\n"
        "• <b>Моя подписка</b> — статус и дата заморозки"
    )
    if active:
        text += f"\n\n✅ Подписка активна, конфигов: {configs_count}"

    await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="HTML")


@router.message(F.text.in_(MAIN_MENU_BUTTONS), StateFilter(QuestionState.waiting_for_question))
async def block_buttons_during_question(message: Message):
    await message.answer(
        
        "⚠️ Сейчас вы задаёте вопрос.\nНапишите его текстом или нажмите «Отмена».",
        reply_markup=get_question_keyboard(),
    )


# --- Подписка ---


@router.message(F.text == "📦 Купить подписку", ~StateFilter(QuestionState.waiting_for_question))
async def buy_subscription(message: Message):
    async with message.bot.get_db_session() as session:
        user = await _require_user(message.from_user.id, message, session)
        if not user:
            return

        if await crud.check_subscription_status(session, user.id):
            sub = await crud.get_user_subscription(session, user.id)
            left = max(0, (sub.next_payment - utcnow()).days)
            await message.answer(
                
                f"✅ У вас уже есть активная подписка.\n"
                f"Осталось дней: {left}\n\n"
                f"Продлить можно кнопкой «Продлить подписку».",
            )
            return

        if await crud.has_pending_payment(session, user.id):
            await message.answer(
                
                "⏳ У вас уже есть неподтверждённый платёж.\n"
                "Дождитесь проверки администратором или отмените его через /cancel.",
            )
            return

        price = subscription_price(1, 1)
        payment = await crud.create_payment(
            session, user.id, type="subscription", amount=price.total, months=1
        )

    text = (
        "📦 <b>Покупка подписки</b>\n\n"
        + render_price_breakdown("", price)
        + "\n\n"
        + f"📥 <b>Скачать клиент:</b> {config.AMNESIA_DOWNLOAD_LINK}\n\n"
        f"🔧 <b>Настройка:</b>\n{config.TUNNEL_INSTRUCTION}\n\n"
        f"💳 <b>Оплата</b>\n"
        f"Карта: <code>{config.CARD_NUMBER}</code>\n"
        f"Получатель: {config.CARD_HOLDER}"
    )
    await message.answer(
         text, reply_markup=get_payment_keyboard(payment.id), parse_mode="HTML"
    )


@router.message(F.text == "🔄 Продлить подписку", ~StateFilter(QuestionState.waiting_for_question))
async def extend_subscription(message: Message):
    async with message.bot.get_db_session() as session:
        user = await _require_user(message.from_user.id, message, session)
        if not user:
            return

        sub = await crud.get_user_subscription(session, user.id)
        if sub is None:
            await message.answer(
                "❌ Подписки пока нет.\nОформите её кнопкой «Купить подписку»."
            )
            return

        if await crud.has_pending_payment(session, user.id):
            await message.answer(
                "⏳ У вас уже есть неподтверждённый платёж.\n"
                "Дождитесь проверки администратором."
            )
            return

        configs_count = await crud.get_active_configs_count(session, user.id)
        keyboard = get_extend_keyboard(configs_count)

        # Просроченную подписку тоже можно продлить: иначе пользователь,
        # которому закрыли доступ, уже не смог бы вернуться.
        if not crud.is_active(sub):
            await message.answer(
                "🔒 Подписка заморожена — доступ к конфигам отключён.\n\n"
                "Оплата вернёт доступ и продлит срок с сегодняшнего дня.\n"
                "Выберите срок:"
            )

    await message.answer(
        f"Выберите срок продления. Конфигов сейчас: {configs_count}.",
        reply_markup=keyboard,
    )


@router.message(F.text == "ℹ️ Моя подписка", ~StateFilter(QuestionState.waiting_for_question))
async def check_subscription(message: Message):
    async with message.bot.get_db_session() as session:
        user = await _require_user(message.from_user.id, message, session)
        if not user:
            return

        sub = await crud.get_user_subscription(session, user.id)
        configs_count = await crud.get_active_configs_count(session, user.id)

        if not crud.is_active(sub):
            if sub:
                await message.answer(
                    
                    "❌ Подписка не активна.\n\n"
                    f"Оплатить нужно было до {config.format_dt(sub.next_payment)}.\n"
                    f"Доступ к конфигам заблокирован.\n\n"
                    "Оформите подписку заново кнопкой «Купить подписку».",
                )
            else:
                await message.answer(
                     "❌ Подписки пока нет.\n\nОформите её кнопкой «Купить подписку»."
                )
            return

        left = max(0, (sub.next_payment - utcnow()).days)

    await message.answer(
        
        "✅ <b>Подписка активна</b>\n\n"
        f"📅 Заморозка: {config.format_dt(sub.next_payment)}\n"
        f"⏰ Осталось дней: {left}\n"
        f"📱 Конфигов: {configs_count}\n"
        f"💰 В месяц: {format_price(monthly_price(configs_count))}",
        parse_mode="HTML",
    )


# --- Оплата ---


@router.callback_query(F.data.startswith("extend:"))
async def extend_period_selected(callback: CallbackQuery):
    months = int(callback.data.split(":")[1])
    await callback.answer()

    if not config.is_allowed_period(months):
        await callback.message.answer("❌ Недоступный срок оплаты.")
        return

    async with callback.bot.get_db_session() as session:
        user = await _require_user(callback.from_user.id, callback.message, session)
        if not user:
            return

        configs_count = await crud.get_active_configs_count(session, user.id)
        price = subscription_price(months, configs_count)
        payment = await crud.create_payment(
            session,
            user.id,
            type="extend",
            amount=price.total,
            months=months,
        )

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"🔄 <b>Продление на {format_months(months)}</b>\n\n"
        + render_price_breakdown("", price)
        + "\n\n"
        f"💳 <b>Оплата</b>\n"
        f"Карта: <code>{config.CARD_NUMBER}</code>\n"
        f"Получатель: {config.CARD_HOLDER}\n\n"
        f"После оплаты нажмите «Я оплатил(а)»",
        reply_markup=get_payment_keyboard(payment.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pay:"))
async def payment_confirmed(callback: CallbackQuery):
    """Пользователь сообщил об оплате: создаём уведомление для админа."""
    await callback.answer()
    payment_id = int(callback.data.split(":")[1])

    async with callback.bot.get_db_session() as session:
        payment = await crud.get_payment(session, payment_id)
        if not payment:
            await callback.message.answer("❌ Платёж не найден. Начните заново.")
            return
        if payment.user_id != await _user_pk(session, callback.from_user.id):
            await callback.message.answer("⛔ Это не ваш платёж.")
            return
        if not payment.is_open:
            await callback.message.answer("ℹ️ По этому платежу уже ответили.")
            return

        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        configs_count = await crud.get_active_configs_count(session, user.id)
        type_text = {
            "subscription": "подписка",
            "extend": f"продление на {format_months(payment.months or 1)}",
            "new_config": f"новый конфиг #{payment.config_number}",
        }.get(payment.type, payment.type)

        admin_text = (
            "💰 <b>Новый платёж</b>\n\n"
            f"Пользователь: {callback.from_user.username and f'@{callback.from_user.username}' or callback.from_user.id}\n"
            f"ID: <code>{callback.from_user.id}</code>\n"
            f"Тип: {type_text}\n"
            f"Сумма: <b>{format_price(payment.amount)}</b>\n"
            f"Конфигов сейчас: {configs_count}\n\n"
            f"Проверьте поступление и подтвердите."
        )
        admin_kb = get_admin_payment_keyboard(payment.id, payment.amount, type_text)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ Спасибо! Уведомление администратору отправлено.\n"
        "Обычно подтверждение занимает до 30 минут."
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await callback.bot.send_message(
                admin_id, admin_text, reply_markup=admin_kb, parse_mode="HTML"
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Не удалось отправить уведомление админу %s: %s", admin_id, e)


async def _user_pk(session, telegram_id: int) -> int:
    user = await crud.get_user_by_telegram_id(session, telegram_id)
    return user.id if user else -1


# --- Конфиги ---


@router.message(F.text == "📱 Мои конфиги", ~StateFilter(QuestionState.waiting_for_question))
async def my_configs(message: Message):
    async with message.bot.get_db_session() as session:
        user = await _require_user(message.from_user.id, message, session)
        if not user:
            return

        configs = await crud.get_user_configs(session, user.id)
        if not configs:
            await message.answer(
                
                "📭 У вас пока нет конфигов.\n\n"
                "Оформите подписку, чтобы получить первый конфиг.",
            )
            return

        sub = await crud.get_user_subscription(session, user.id)
        lines = [f"📱 <b>Ваши конфиги ({len(configs)})</b>\n"]
        for cfg in configs:
            icon = "🔒" if cfg.is_protected else "📱"
            until = f" — до {config.format_dt(cfg.paid_until)}" if cfg.paid_until else ""
            lines.append(f"{icon} Конфиг #{cfg.config_number}{until}")
        if sub and not crud.is_active(sub):
            lines.append("\n⚠️ Подписка не активна — доступ к конфигам заблокирован.")
        lines.append(f"\n💰 В месяц: {format_price(monthly_price(len(configs)))}")

    await message.answer(
         "\n".join(lines), reply_markup=get_configs_keyboard(configs), parse_mode="HTML"
    )


@router.callback_query(F.data == "cfgnew")
async def buy_extra_config(callback: CallbackQuery):
    await callback.answer()

    async with callback.bot.get_db_session() as session:
        user = await _require_user(callback.from_user.id, callback.message, session)
        if not user:
            return

        if not await crud.check_subscription_status(session, user.id):
            await callback.message.answer(
                "❌ Докупка конфигов доступна только с активной подпиской."
            )
            return

        if await crud.has_pending_payment(session, user.id):
            await callback.message.answer(
                "⏳ У вас уже есть неподтверждённый платёж.\nДождитесь проверки администратором."
            )
            return

        days_left = await crud.days_left(session, user.id)
        price, explanation = extra_config_price(days_left)
        next_number = await crud.get_next_config_number(session, user.id)

        payment = await crud.create_payment(
            session,
            user.id,
            type="new_config",
            amount=price,
            config_number=next_number,
        )

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"➕ <b>Докупка конфига #{next_number}</b>\n\n"
        f"{explanation}\n\n"
        f"💳 <b>Оплата</b>\n"
        f"Карта: <code>{config.CARD_NUMBER}</code>\n"
        f"Получатель: {config.CARD_HOLDER}\n\n"
        f"После оплаты нажмите «Я оплатил(а)»",
        reply_markup=get_payment_keyboard(payment.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cfglist")
async def back_to_configs(callback: CallbackQuery):
    await callback.answer()

    async with callback.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, callback.from_user.id)
        if not user:
            return
        configs = await crud.get_user_configs(session, user.id)

        lines = [f"📱 <b>Ваши конфиги ({len(configs)})</b>\n"]
        for cfg in configs:
            icon = "🔒" if cfg.is_protected else "📱"
            until = f" — до {config.format_dt(cfg.paid_until)}" if cfg.paid_until else ""
            lines.append(f"{icon} Конфиг #{cfg.config_number}{until}")
        lines.append(f"\n💰 В месяц: {format_price(monthly_price(len(configs)))}")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=get_configs_keyboard(configs), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("cfg:"))
async def select_config(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    await callback.answer()

    async with callback.bot.get_db_session() as session:
        user_pk = await _user_pk(session, callback.from_user.id)
        cfg = await crud.get_config_by_id(session, config_id, user_pk)
        if not cfg or not cfg.is_active:
            await callback.message.answer("❌ Конфиг не найден.")
            return

        sub = await crud.get_user_subscription(session, cfg.user_id)
        paid = cfg.paid_until is not None and cfg.paid_until > utcnow()
        blocked = not crud.is_active(sub)

        status = "✅ Активен"
        if cfg.paid_until and not paid:
            status = "⚠️ Оплата просрочена"
        if blocked:
            status = "🔒 Заблокирован (нет активной подписки)"

        text = (
            f"📱 <b>Конфиг #{cfg.config_number}</b>\n\n"
            f"Создан: {format_date(config.local(cfg.created_at))}\n"
            f"Оплачен до: {config.format_dt(cfg.paid_until)}\n"
            f"Статус: {status}\n"
            + ("🔒 Основной конфиг, его нельзя удалить\n" if cfg.is_protected else "")
        )
        kb = get_config_actions_keyboard(cfg)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("link:"))
async def show_config_link(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    await callback.answer()

    async with callback.bot.get_db_session() as session:
        user_pk = await _user_pk(session, callback.from_user.id)
        cfg = await crud.get_config_by_id(session, config_id, user_pk)
        if not cfg or not cfg.is_active or not cfg.vless_link:
            await callback.message.answer("❌ Конфиг не найден.")
            return

        sub = await crud.get_user_subscription(session, cfg.user_id)
        if not crud.is_active(sub):
            await callback.message.answer("❌ Доступ закрыт: нет активной подписки.")
            return
        if cfg.paid_until and cfg.paid_until <= utcnow():
            await callback.message.answer("❌ Оплата за этот конфиг просрочена.")
            return

        link = cfg.vless_link
        number = cfg.config_number

    await callback.message.answer(
        f"🔗 <b>Конфиг #{number}</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"Скопируйте ссылку и добавьте в VPN-клиент.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("del:"))
async def delete_config(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    await callback.answer()

    async with callback.bot.get_db_session() as session:
        user_pk = await _user_pk(session, callback.from_user.id)
        cfg = await crud.get_config_by_id(session, config_id, user_pk)
        if not cfg or not cfg.is_active:
            await callback.message.answer("❌ Конфиг не найден.")
            return
        if cfg.is_protected:
            await callback.message.answer("❌ Основной конфиг удалить нельзя.")
            return

        name, number = cfg.config_name, cfg.config_number
        ok = await crud.deactivate_config(session, config_id, user_pk)
        if not ok:
            await callback.message.answer("❌ Не удалось удалить конфиг.")
            return

    from bot.api.client import AdminAPIError, admin_api

    try:
        await admin_api.delete_user(name)
        admin_note = "✅ Конфиг удалён, доступ к VPN отозван."
    except AdminAPIError as e:
        # Не роняем сценарий: в БД конфиг уже деактивирован, но админку надо поправить
        admin_note = "⚠️ Конфиг убран из бота, но удалить его в админке не вышло."
        logger.error("Не удалось удалить %s из админки: %s", name, e)

    await callback.message.edit_text(
        f"🗑 Конфиг #{number} удалён.\n\n{admin_note}\n"
        f"Новый конфиг можно докупить кнопкой ниже.",
        reply_markup=get_configs_keyboard([]),
    )


# --- Вопросы ---


@router.message(F.text == "❓ Задать вопрос", ~StateFilter(QuestionState.waiting_for_question))
async def ask_question(message: Message, state: FSMContext):
    await state.set_state(QuestionState.waiting_for_question)
    await message.answer(
        
        "📝 Напишите ваш вопрос текстом — администратор ответит в этот чат.",
        reply_markup=get_question_keyboard(),
    )


@router.message(QuestionState.waiting_for_question, F.text)
async def process_question(message: Message, state: FSMContext):
    await state.clear()

    who = message.from_user.username and f"@{message.from_user.username}" or message.from_user.id
    text = (
        "❓ <b>Новый вопрос</b>\n\n"
        f"От: {who}\n"
        f"ID: <code>{message.from_user.id}</code>\n\n"
        f"{message.text}"
    )

    delivered = False
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, text, parse_mode="HTML")
            delivered = True
        except Exception as e:  # noqa: BLE001
            logger.error("Не удалось отправить вопрос админу %s: %s", admin_id, e)

    await message.answer(
        
        "✅ Вопрос отправлен администратору."
        if delivered
        else "⚠️ Не удалось доставить вопрос. Напишите администратору лично.",
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("✅ Действие отменено.")


# --- Служебное ---


@router.callback_query(F.data == "close")
async def close_message(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001
        await callback.message.edit_reply_markup(reply_markup=None)


# --- Ловушка для всего, что не нашлось выше ---


@router.callback_query()
async def unknown_callback(callback: CallbackQuery):
    """Молчание — худший вариант: клиент не понимает, что нажал, и мы не знаем почему.

    Ловим незнакомый callback_data и пишем его в лог, чтобы чинить по факту.
    """
    logger.warning(
        "Неизвестный callback %r от %s (чат %s)",
        callback.data, callback.from_user.id, callback.message.chat.id,
    )
    await callback.answer("Кнопка устарела. Откройте /start и попробуйте снова.", show_alert=True)


@router.message()
async def unknown_message(message: Message):
    logger.warning(
        "Неизвестное сообщение %r от %s", message.text, message.from_user.id
    )
