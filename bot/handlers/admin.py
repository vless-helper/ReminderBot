"""Админские хендлеры.

Главное изменение: подтверждение оплаты берёт все данные из записи Payment в БД
по payment_id, который приходит в callback_data. Раньше здесь читался FSM
админа, из-за чего при работе с чужим аккаунтом (не совпадающим с аккаунтом
плательщика) тип платежа и срок всегда терялись.
"""

import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.api.client import AdminAPIError, admin_api
from bot.config import config, utcnow
from bot.db import crud
from bot.keyboards.keyboards import get_admin_payment_keyboard, get_retry_keyboard
from bot.utils.admin_utils import get_args, require_admin
from bot.utils.helpers import format_months, format_price

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in config.ADMIN_IDS


def _payment_title(payment) -> str:
    if payment.type == "extend":
        return f"продление на {format_months(payment.months or 1)}"
    if payment.type == "new_config":
        return f"конфиг #{payment.config_number}"
    return "подписка"


async def _apply_payment_to_user(session, payment, bot) -> str:
    """Применить оплаченное действие. Возвращает текст для пользователя.

    Порядок важен: сначала эффект в БД (и отметка payment.effect_applied),
    затем обращения к админке. Поэтому повторная обработка не продлит
    подписку дважды, а лишь пересоздаст/разблокирует конфиг заново.
    """
    user_id = payment.user_id

    # --- Эффект в БД ---
    already_applied = payment.effect_applied
    vless_link = None
    name = None
    number = 1

    if not already_applied:
        if payment.type == "extend":
            months = payment.months or 1
            await crud.extend_subscription_months(session, user_id, months)
            days = config.days_for_months(months)
            await crud.extend_all_configs_paid_until(session, user_id, days)
        elif payment.type == "subscription":
            # Первая подписка
            await crud.reactivate_subscription(session, user_id, config.SUBSCRIPTION_DAYS)
            existing = await crud.get_user_configs(session, user_id)
            if not existing:
                number = payment.config_number or 1
                name = f"user_{await _tg_id(session, user_id)}_{number}"
        # new_config подписку НЕ трогает: докупка конфига не продлевает период.
        # Раньше сюда попадал любой не-extend тип и вызывался
        # reactivate_subscription, из-за чего каждая докупка добавляла к подписке
        # ещё SUBSCRIPTION_DAYS. Срок нового конфига берётся из конца подписки ниже.

        await crud.mark_effect_applied(session, payment.id)

    # --- Внешние вызовы ---
    if payment.type == "new_config":
        number = payment.config_number or 1
        name = f"user_{await _tg_id(session, user_id)}_{number}"
        cfg = await crud.get_config_by_name(session, name)
        # Для только что созданного конфига cfg ещё None, поэтому срок держим
        # отдельно — иначе в сообщении показывалось «Оплачен до: —».
        paid_until = cfg.paid_until if cfg else None

        if cfg and cfg.vless_link:
            vless_link = cfg.vless_link
        else:
            # get_or_create идемпотентен: если POST уже прошёл, ссылка просто
            # заберётся заново, второй пользователь не создастся
            vless_link = await admin_api.get_or_create_user_config(name)
            if cfg:
                cfg.vless_link = vless_link
                await session.commit()
            else:
                # Докупленный конфиг оплачен ровно до конца текущей подписки.
                sub = await crud.get_user_subscription(session, user_id)
                paid_until = (
                    sub.next_payment
                    if sub and sub.next_payment > utcnow()
                    else utcnow() + timedelta(days=config.SUBSCRIPTION_DAYS)
                )
                await crud.create_client_config(
                    session, user_id, number, name, vless_link,
                    is_protected=False, paid_until=paid_until,
                )

        return (
            f"✅ <b>Конфиг #{number} создан</b>\n\n"
            f"🔗 <code>{vless_link}</code>\n\n"
            f"Оплачен до: {config.format_dt(paid_until) if paid_until else '—'}\n\n"
            f"Ссылка продублирована в разделе «Мои конфиги»."
        )

    if payment.type == "extend":
        months = payment.months or 1
    else:
        months = None

    # Разблокируем конфиги: операция идемпотентна, поэтому её безопасно повторять
    configs = await crud.get_user_configs(session, user_id)
    failed_to_unlock: list[str] = []
    for cfg in configs:
        try:
            await admin_api.unarchive_user(cfg.config_name)
        except AdminAPIError as e:
            failed_to_unlock.append(cfg.config_name)
            logger.error("Не удалось разблокировать %s: %s", cfg.config_name, e)

    if failed_to_unlock:
        # Подписка продлена, но доступ не выдан. Поднимаем ошибку, чтобы платёж
        # ушёл в failed с кнопкой «Повторить»: подписка повторно не продлится
        # (effect_applied), а разблокировка повторится.
        raise AdminAPIError(
            "Не удалось вернуть доступ к конфигам: " + ", ".join(failed_to_unlock)
        )

    sub = await crud.get_user_subscription(session, user_id)

    if payment.type == "extend":
        return (
            f"✅ <b>Оплата подтверждена</b>\n\n"
            f"Подписка продлена на {format_months(months)}.\n"
            f"Доступ к VPN восстановлен."
        )

    if not configs:
        number = payment.config_number or 1
        name = f"user_{await _tg_id(session, user_id)}_{number}"
        vless_link = await admin_api.get_or_create_user_config(name)
        await crud.create_client_config(
            session, user_id, number, name, vless_link,
            is_protected=True, paid_until=sub.next_payment,
        )
        return (
            f"✅ <b>Подписка активирована</b>\n\n"
            f"Срок: до {config.format_dt(sub.next_payment)}\n\n"
            f"🔗 <code>{vless_link}</code>\n\n"
            f"Ссылка также в разделе «Мои конфиги»."
        )

    return (
        f"✅ <b>Оплата подтверждена</b>\n\n"
        f"Подписка активна до {config.format_dt(sub.next_payment)}.\n"
        f"Доступ к конфигам восстановлен."
    )


async def _tg_id(session, user_pk: int) -> int:
    from sqlalchemy import select

    from bot.db.models import User

    return (
        await session.execute(select(User.telegram_id).where(User.id == user_pk))
    ).scalar_one()


# --- Подтверждение / отклонение ---


async def _process_payment(callback: CallbackQuery, payment_id: int) -> None:
    """Общая часть подтверждения и повторной обработки."""
    async with callback.bot.get_db_session() as session:
        claimed = await crud.claim_payment(session, payment_id, callback.from_user.id)
        if claimed is None:
            await callback.answer("По этому платежу уже ответили", show_alert=True)
            return

        payment = await crud.get_payment(session, payment_id)
        tg_id = await _tg_id(session, payment.user_id)

        try:
            text = await _apply_payment_to_user(session, payment, callback.bot)
        except AdminAPIError as e:
            # Деньги зачислены, доступ не выдали. Не теряем платёж: помечаем
            # failed и даём админу кнопку «Повторить» — эффекты в БД при этом
            # не применяются дважды (payment.effect_applied).
            logger.error("Ошибка применения платежа %s: %s", payment_id, e)
            await crud.fail_payment(session, payment_id, str(e))

            await _edit_admin_message(
                callback,
                f"⚠️ Платёж #{payment_id} зачислен, но доступ не выдан.\n{e}",
                reply_markup=get_retry_keyboard(payment_id),
            )
            await callback.answer("⚠️ Ошибка — есть кнопка «Повторить»", show_alert=True)
            return

        await crud.complete_payment(session, payment_id)

    await callback.answer("✅ Подтверждено", show_alert=True)
    await _edit_admin_message(callback, f"✅ Платёж #{payment_id} подтверждён.")

    try:
        await callback.bot.send_message(tg_id, text, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        logger.error("Не удалось отправить подтверждение пользователю %s: %s", tg_id, e)
        for admin_id in config.ADMIN_IDS:
            try:
                await callback.bot.send_message(
                    admin_id,
                    f"⚠️ Платёж #{payment_id} обработан, но уведомление пользователю "
                    f"{tg_id} не дошло: {e}",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось отправить алерт админу %s", admin_id)


@router.callback_query(F.data.startswith("ok:"))
async def confirm_payment(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await _process_payment(callback, int(callback.data.split(":")[1]))


@router.callback_query(F.data.startswith("retry:"))
async def retry_payment(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return
    await _process_payment(callback, int(callback.data.split(":")[1]))


@router.callback_query(F.data.startswith("no:"))
async def reject_payment(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[1])

    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    async with callback.bot.get_db_session() as session:
        payment = await crud.get_payment(session, payment_id)
        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return

        if payment.status in {"completed", "rejected"}:
            await callback.answer("По этому платежу уже ответили", show_alert=True)
            return

        # Отклонять можно только пока эффект не применён
        if payment.effect_applied:
            await callback.answer(
                "Эффект уже применён, отклонить нельзя — разберитесь вручную",
                show_alert=True,
            )
            return

        payment.status = "rejected"
        payment.resolved_at = utcnow()
        payment.resolved_by = callback.from_user.id
        await session.commit()

        tg = await _tg_id(session, payment.user_id)

    await callback.answer("❌ Отклонено", show_alert=True)
    await _edit_admin_message(callback, f"❌ Платёж #{payment_id} отклонён.")

    await callback.bot.send_message(
        tg,
        "❌ <b>Оплата не подтверждена</b>\n\n"
        "Возможно, реквизиты отличаются от указанных. "
        "Напишите администратору — разберёмся.",
        parse_mode="HTML",
    )


async def _edit_admin_message(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception:  # noqa: BLE001
        logger.debug("Не удалось отредактировать сообщение админа", exc_info=True)


# --- Команды ---


@router.message(Command("answer"))
async def answer_question(message: Message):
    if not await require_admin(message):
        return

    args, error = get_args(
        message,
        min_args=2,
        usage=(
            "❌ Использование: /answer [telegram_id] [текст]\n"
            "Пример: /answer 123456789 Спасибо за вопрос!"
        ),
    )
    if error:
        await message.answer(error)
        return

    tg_id = int(args[0])
    text = " ".join(args[1:])

    try:
        await message.bot.send_message(tg_id, f"📨 <b>Ответ администратора</b>\n\n{text}", parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"❌ Не удалось отправить: {e}")
        return

    await message.answer(f"✅ Отправлено пользователю {tg_id}")


@router.message(Command("pending"))
async def list_pending(message: Message):
    """Показать все неподтверждённые платежи — чтобы ничего не потерялось."""
    if not await require_admin(message):
        return

    async with message.bot.get_db_session() as session:
        from sqlalchemy import select

        from bot.db.models import Payment, User

        stmt = (
            select(Payment, User)
            .join(User, User.id == Payment.user_id)
            .where(Payment.status == "pending")
            .order_by(Payment.created_at)
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        await message.answer("✅ Неподтверждённых платежей нет.")
        return

    lines = ["⏳ <b>Неподтверждённые платежи</b>\n"]
    for payment, user in rows:
        kb = get_admin_payment_keyboard(payment.id, payment.amount, _payment_title(payment))
        lines.append(
            f"• #{payment.id} — {user.username and f'@{user.username}' or user.telegram_id} "
            f"({_payment_title(payment)}): {format_price(payment.amount)}"
        )
        await message.answer(
            f"Платёж #{payment.id} — {_payment_title(payment)}\n"
            f"Пользователь: {user.username and f'@{user.username}' or user.telegram_id}\n"
            f"ID: <code>{user.telegram_id}</code>\n"
            f"Сумма: {format_price(payment.amount)}\n"
            f"Создан: {config.format_dt(payment.created_at, with_time=True)}",
            reply_markup=kb,
            parse_mode="HTML",
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def stats(message: Message):
    if not await require_admin(message):
        return

    from sqlalchemy import func, select

    from bot.db.models import ClientConfig, Payment, Subscription, User

    async with message.bot.get_db_session() as session:
        total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
        active = (
            await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == "active", Subscription.next_payment > utcnow()
                )
            )
        ).scalar_one()
        expired = (
            await session.execute(
                select(func.count(Subscription.id)).where(Subscription.next_payment <= utcnow())
            )
        ).scalar_one()
        configs = (
            await session.execute(select(func.count(ClientConfig.id)).where(ClientConfig.is_active.is_(True)))
        ).scalar_one()
        pending = (
            await session.execute(select(func.count(Payment.id)).where(Payment.status == "pending"))
        ).scalar_one()
        revenue = (
            await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "completed")
            )
        ).scalar_one()

    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: {total_users}\n"
        f"Активных подписок: {active}\n"
        f"Просрочено: {expired}\n"
        f"Активных конфигов: {configs}\n"
        f"Ожидают подтверждения: {pending}\n"
        f"Принято оплат всего: {format_price(revenue)}",
        parse_mode="HTML",
    )


@router.message(Command("user"))
async def user_info(message: Message):
    if not await require_admin(message):
        return

    args, error = get_args(
        message, min_args=1, usage="❌ Использование: /user [telegram_id]"
    )
    if error:
        await message.answer(error)
        return

    tg_id = int(args[0])

    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer(f"❌ Пользователь {tg_id} не найден")
            return

        sub = await crud.get_user_subscription(session, user.id)
        configs = await crud.get_user_configs(session, user.id)
        pendings = await crud.get_pending_payments(session, user.id)

    state = "активна" if crud.is_active(sub) else "не активна"
    sub_text = (
        f"{state}, до {config.format_dt(sub.next_payment)}" if sub else "нет подписки"
    )

    await message.answer(
        f"👤 <b>{user.username and f'@{user.username}' or tg_id}</b>\n\n"
        f"Подписка: {sub_text}\n"
        f"Конфигов: {len(configs)}"
        + ("\n• " + "\n• ".join(f"#{c.config_number} до {config.format_dt(c.paid_until)}" for c in configs) if configs else "")
        + (f"\n\n⏳ Неподтверждённых платежей: {len(pendings)}" if pendings else ""),
        parse_mode="HTML",
    )


@router.message(Command("admin_status"))
async def admin_status(message: Message):
    if not await require_admin(message):
        return

    if await admin_api.health_check():
        await message.answer("✅ Админка доступна")
    else:
        await message.answer("❌ Админка недоступна. Проверьте сервер.")


@router.message(Command("check_reminders"))
async def check_reminders_now(message: Message):
    if not await require_admin(message):
        return

    from bot.reminder import check_and_send_reminders

    await message.answer("🔄 Проверяю напоминания...")
    try:
        sent = await check_and_send_reminders(message.bot, message.bot.session_maker)
        await message.answer(f"✅ Готово. Отправлено уведомлений: {sent}")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("reset_reminder"))
async def reset_reminder(message: Message):
    if not await require_admin(message):
        return

    args, error = get_args(
        message, min_args=1, usage="❌ Использование: /reset_reminder [telegram_id]"
    )
    if error:
        await message.answer(error)
        return

    tg_id = int(args[0])

    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer(f"❌ Пользователь {tg_id} не найден")
            return
        sub = await crud.get_user_subscription(session, user.id)
        if not sub:
            await message.answer("❌ У пользователя нет подписки")
            return
        await crud.reset_reminder_flags(session, sub.id)

    await message.answer("✅ Флаги напоминаний сброшены")


@router.message(Command("set_payment_date"))
async def set_payment_date(message: Message):
    """Подвинуть дату заморозки — для тестов и ручных разборов."""
    if not await require_admin(message):
        return

    args, error = get_args(
        message,
        min_args=2,
        usage=(
            "❌ Использование: /set_payment_date [telegram_id] [дней]\n"
            "Положительное число — отодвинуть в будущее, отрицательное — в прошлое."
        ),
    )
    if error:
        await message.answer(error)
        return

    tg_id = int(args[0])
    days = int(args[1])

    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer(f"❌ Пользователь {tg_id} не найден")
            return
        sub = await crud.get_user_subscription(session, user.id)
        if not sub:
            await message.answer("❌ У пользователя нет подписки")
            return

        sub.next_payment = utcnow() + timedelta(days=days)
        sub.status = "active"
        await session.commit()

        await crud.reset_reminder_flags(session, sub.id)

        configs = await crud.get_user_configs(session, user.id)
        for cfg in configs:
            cfg.paid_until = sub.next_payment
        await session.commit()

    await message.answer(
        f"✅ Новая дата заморозки: {config.format_dt(sub.next_payment, with_time=True)}\n"
        f"Обновлено конфигов: {len(configs)}"
    )


@router.message(Command("unblock"))
async def unblock_user(message: Message):
    """Разблокировать конфиги в админке вручную."""
    if not await require_admin(message):
        return

    args, error = get_args(
        message,
        min_args=1,
        usage="❌ Использование: /unblock [telegram_id] — разблокирует все конфиги",
    )
    if error:
        await message.answer(error)
        return

    tg_id = int(args[0])

    async with message.bot.get_db_session() as session:
        user = await crud.get_user_by_telegram_id(session, tg_id)
        if not user:
            await message.answer(f"❌ Пользователь {tg_id} не найден")
            return
        configs = await crud.get_user_configs(session, user.id)
        names = [c.config_name for c in configs]

    done, failed = [], []
    for name in names:
        try:
            await admin_api.unarchive_user(name)
            done.append(name)
        except AdminAPIError as e:
            failed.append(f"{name}: {e}")

    await message.answer(
        f"✅ Разблокировано: {len(done)}\n" + (f"❌ Ошибки: {'; '.join(failed)}" if failed else "")
    )


@router.message(Command("sync"))
async def sync_with_admin(message: Message):
    """Сверить рассинхрон между ботом и админкой."""
    if not await require_admin(message):
        return

    from bot.db.models import ClientConfig
    from sqlalchemy import select

    async with message.bot.get_db_session() as session:
        bot_names = set(
            (
                await session.execute(
                    select(ClientConfig.config_name).where(ClientConfig.is_active.is_(True))
                )
            ).scalars().all()
        )
        try:
            admin_users = await admin_api.get_all_users()
        except AdminAPIError as e:
            await message.answer(f"❌ Админка недоступна: {e}")
            return

    admin_map = {u.get("name"): u for u in admin_users}
    only_admin = sorted(set(admin_map) - bot_names)
    only_bot = sorted(bot_names - set(admin_map))

    lines = ["🔄 <b>Сверка</b>\n"]
    lines.append(f"В боте: {len(bot_names)}, в админке: {len(admin_map)}\n")
    if only_bot:
        lines.append("Есть в боте, нет в админке:\n" + "\n".join(f"• {n}" for n in only_bot))
    if only_admin:
        lines.append("\nЕсть в админке, нет в боте:\n" + "\n".join(f"• {n}" for n in only_admin))
    if not only_bot and not only_admin:
        lines.append("✅ Расхождений нет")

    await message.answer("\n".join(lines), parse_mode="HTML")
