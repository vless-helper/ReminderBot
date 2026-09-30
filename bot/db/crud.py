"""Доступ к данным. Никакой бизнес-логики в хендлерах.

Ключевые отличия от прошлой версии:
  * все даты — aware UTC (раньше смешивались naive и timezone-aware, что ломало
    работу на PostgreSQL с TypeError);
  * платёж создаётся сразу с типом/суммой/месяцами и админ подтверждает его по
    payment_id (раньше эти данные жили в FSM плательщика и терялись);
  * у напоминаний раздельные отметки на 3 дня / 1 день / истечение.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.config import config, utcnow
from bot.db.models import ClientConfig, Payment, ReminderLog, Subscription, User

# --- Пользователи ---


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
) -> User:
    stmt = select(User).where(User.telegram_id == telegram_id)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if user:
        # username меняется, а раньше он обновлялся только при создании
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if changed:
            await session.commit()
        return user

    user = User(telegram_id=telegram_id, username=username, full_name=full_name)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
    stmt = select(User).where(User.telegram_id == telegram_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_all_users(session: AsyncSession) -> list[User]:
    stmt = select(User).options(selectinload(User.subscription)).order_by(User.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


# --- Подписки ---


async def get_user_subscription(session: AsyncSession, user_id: int) -> Optional[Subscription]:
    stmt = select(Subscription).where(Subscription.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def is_active(subscription: Optional[Subscription], at: Optional[datetime] = None) -> bool:
    return bool(
        subscription
        and subscription.status == "active"
        and subscription.next_payment > (at or utcnow())
    )


async def check_subscription_status(session: AsyncSession, user_id: int) -> bool:
    return is_active(await get_user_subscription(session, user_id))


async def days_left(session: AsyncSession, user_id: int) -> int:
    """Полных дней до заморозки подписки. 0, если подписки нет или она истекла."""
    sub = await get_user_subscription(session, user_id)
    if not sub:
        return 0
    delta = sub.next_payment - utcnow()
    return max(0, delta.days)


async def extend_subscription_months(
    session: AsyncSession, user_id: int, months: int
) -> Subscription:
    """Продлить подписку на N месяцев от текущей даты заморозки.

    Просроченная подписка продлевается от «сейчас», активная — от её конца.
    """
    if not config.is_allowed_period(months):
        raise ValueError(f"Недопустимый срок продления: {months}")

    days = config.days_for_months(months)
    now = utcnow()

    sub = await get_user_subscription(session, user_id)

    if sub is None:
        sub = Subscription(
            user_id=user_id,
            next_payment=now + timedelta(days=days),
            status="active",
            period_days=days,
        )
        session.add(sub)
    else:
        base = sub.next_payment if sub.next_payment > now else now
        sub.next_payment = base + timedelta(days=days)
        sub.status = "active"
        sub.period_days = days

    await session.commit()
    await session.refresh(sub)

    # Новый период — старые отметки о напоминаниях больше неактуальны
    await reset_reminder_flags(session, sub.id)
    return sub


async def reactivate_subscription(session: AsyncSession, user_id: int, days: int) -> Subscription:
    """Продление на фиксированное число дней (первая подписка)."""
    now = utcnow()
    sub = await get_user_subscription(session, user_id)
    if sub is None:
        sub = Subscription(
            user_id=user_id,
            next_payment=now + timedelta(days=days),
            status="active",
            period_days=days,
        )
        session.add(sub)
    else:
        base = sub.next_payment if sub.next_payment > now else now
        sub.next_payment = base + timedelta(days=days)
        sub.status = "active"
        sub.period_days = days

    await session.commit()
    await session.refresh(sub)
    await reset_reminder_flags(session, sub.id)
    return sub


# --- Платежи ---


async def create_payment(
    session: AsyncSession,
    user_id: int,
    type: str,
    amount: int,
    months: Optional[int] = None,
    config_number: Optional[int] = None,
    comment: Optional[str] = None,
) -> Payment:
    """Создать pending-платёж. Всё нужное для подтверждения — внутри записи."""
    payment = Payment(
        user_id=user_id,
        type=type,
        status="pending",
        amount=amount,
        months=months,
        config_number=config_number,
        comment=comment,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def get_payment(session: AsyncSession, payment_id: int) -> Optional[Payment]:
    stmt = select(Payment).where(Payment.id == payment_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_last_pending_payment(session: AsyncSession, user_id: int) -> Optional[Payment]:
    """Оставлен для совместимости, но в логике подтверждения больше не используется —
    берём платёж по конкретному id из callback_data."""
    stmt = (
        select(Payment)
        .where(Payment.user_id == user_id, Payment.status == "pending")
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_pending_payments(session: AsyncSession, user_id: int) -> list[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.user_id == user_id, Payment.status == "pending")
        .order_by(Payment.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def has_pending_payment(session: AsyncSession, user_id: int, type: Optional[str] = None) -> bool:
    """Защита от дублей: не даём создать второй платёж, пока не разберутся с первым."""
    stmt = select(func.count(Payment.id)).where(Payment.user_id == user_id, Payment.status == "pending")
    if type:
        stmt = stmt.where(Payment.type == type)
    return (await session.execute(stmt)).scalar_one() > 0


async def claim_payment(session: AsyncSession, payment_id: int, admin_id: int) -> Optional[Payment]:
    """Атомарно забрать платёж в работу. None — если его уже забрали/закрыли.

    UPDATE ... WHERE status IN (pending, failed) защищает от двойного нажатия
    двумя админами и от повторной обработки после рестарта.
    """
    stmt = (
        update(Payment)
        .where(Payment.id == payment_id, Payment.status.in_(["pending", "failed"]))
        .values(
            status="processing",
            resolved_at=utcnow(),
            resolved_by=admin_id,
            error=None,
        )
    )
    result = await session.execute(stmt)
    if result.rowcount != 1:
        await session.rollback()
        return None

    await session.commit()
    return await get_payment(session, payment_id)


async def mark_effect_applied(session: AsyncSession, payment_id: int) -> None:
    """Эффект в БД применён — дальше повторять его нельзя."""
    await session.execute(
        update(Payment).where(Payment.id == payment_id).values(effect_applied=True)
    )
    await session.commit()


async def complete_payment(session: AsyncSession, payment_id: int) -> None:
    await session.execute(
        update(Payment)
        .where(Payment.id == payment_id)
        .values(status="completed", error=None)
    )
    await session.commit()


async def fail_payment(session: AsyncSession, payment_id: int, error: str) -> None:
    await session.execute(
        update(Payment)
        .where(Payment.id == payment_id)
        .values(status="failed", error=error[:512])
    )
    await session.commit()


async def reset_stuck_payments(session: AsyncSession) -> int:
    """Вернуть в pending платежи, застрявшие в processing (бот упал в процессе)."""
    result = await session.execute(
        update(Payment).where(Payment.status == "processing").values(status="pending")
    )
    await session.commit()
    return result.rowcount


# --- Напоминания ---

EXPIRED_MARKER = -1


def _day_window(days_before: int) -> tuple[datetime, datetime]:
    """UTC-границы календарного дня в локальной таймзоне, смещённого на days_before.

    Считаем по календарным дням пользователя: «истекает завтра» должно значить
    календарный завтрак, а не «через 24 часа».
    """
    now_local = config.to_local_naive(utcnow())
    target_day = (now_local + timedelta(days=days_before)).date()

    start_local = datetime.combine(target_day, datetime.min.time())
    end_local = datetime.combine(target_day, datetime.max.time())
    return (
        start_local.replace(tzinfo=config.tz).astimezone(timezone.utc),
        end_local.replace(tzinfo=config.tz).astimezone(timezone.utc),
    )


async def get_due_reminder_subscriptions(
    session: AsyncSession, days_before: int
) -> list[Subscription]:
    """Подписки, которым сегодня нужно отправить напоминание за days_before дней."""
    start_utc, end_utc = _day_window(days_before)

    already = select(ReminderLog.subscription_id).where(ReminderLog.days_before == days_before)

    stmt = (
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == "active",
                Subscription.next_payment >= start_utc,
                Subscription.next_payment <= end_utc,
                Subscription.id.notin_(already),
            )
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_expired_subscriptions(session: AsyncSession) -> list[Subscription]:
    """Активные подписки, срок которых прошёл и которым ещё не сообщили."""
    already = select(ReminderLog.subscription_id).where(ReminderLog.days_before == EXPIRED_MARKER)

    stmt = (
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == "active",
                Subscription.next_payment <= utcnow(),
                Subscription.id.notin_(already),
            )
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def mark_reminder_sent(
    session: AsyncSession, subscription_id: int, days_before: int
) -> None:
    """Отметить напоминание. Повторный вызов не создаёт дубль."""
    existing = await session.execute(
        select(ReminderLog.id).where(
            ReminderLog.subscription_id == subscription_id,
            ReminderLog.days_before == days_before,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return

    session.add(ReminderLog(subscription_id=subscription_id, days_before=days_before))
    await session.commit()


async def reset_reminder_flags(session: AsyncSession, subscription_id: int) -> None:
    await session.execute(
        delete(ReminderLog).where(ReminderLog.subscription_id == subscription_id)
    )
    await session.commit()


async def expire_subscription(session: AsyncSession, subscription_id: int) -> None:
    await mark_reminder_sent(session, subscription_id, EXPIRED_MARKER)
    await session.execute(
        update(Subscription).where(Subscription.id == subscription_id).values(status="expired")
    )
    await session.commit()


# --- Конфиги ---


async def get_user_configs(session: AsyncSession, user_id: int) -> list[ClientConfig]:
    stmt = (
        select(ClientConfig)
        .where(ClientConfig.user_id == user_id, ClientConfig.is_active.is_(True))
        .order_by(ClientConfig.config_number)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_all_active_configs(session: AsyncSession) -> list[ClientConfig]:
    stmt = select(ClientConfig).where(ClientConfig.is_active.is_(True)).order_by(ClientConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_config_by_id(
    session: AsyncSession, config_id: int, user_id: Optional[int] = None
) -> Optional[ClientConfig]:
    stmt = select(ClientConfig).where(ClientConfig.id == config_id)
    if user_id is not None:
        stmt = stmt.where(ClientConfig.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_config_by_name(session: AsyncSession, config_name: str) -> Optional[ClientConfig]:
    """Найти конфиг по имени из админки — для идемпотентности подтверждения."""
    stmt = select(ClientConfig).where(ClientConfig.config_name == config_name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_active_configs_count(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(ClientConfig.id)).where(
        ClientConfig.user_id == user_id, ClientConfig.is_active.is_(True)
    )
    return (await session.execute(stmt)).scalar_one()


async def get_next_config_number(session: AsyncSession, user_id: int) -> int:
    """Следующий свободный номер. Учитывает и удалённые конфиги, чтобы не плодить дубли."""
    stmt = select(func.max(ClientConfig.config_number)).where(ClientConfig.user_id == user_id)
    current = (await session.execute(stmt)).scalar_one()
    return (current or 0) + 1


async def create_client_config(
    session: AsyncSession,
    user_id: int,
    config_number: int,
    config_name: str,
    vless_link: str,
    is_protected: bool = False,
    paid_until: Optional[datetime] = None,
) -> ClientConfig:
    sub = await get_user_subscription(session, user_id)

    cfg = ClientConfig(
        user_id=user_id,
        subscription_id=sub.id if sub else None,
        config_number=config_number,
        config_name=config_name,
        vless_link=vless_link,
        is_active=True,
        is_protected=is_protected,
        paid_until=paid_until,
    )
    session.add(cfg)
    await session.flush()

    if sub:
        sub.active_configs_count = await get_active_configs_count(session, user_id)

    await session.commit()
    await session.refresh(cfg)
    return cfg


async def deactivate_config(session: AsyncSession, config_id: int, user_id: int) -> bool:
    """Деактивировать конфиг. Защищённый (первый) удалить нельзя."""
    cfg = await get_config_by_id(session, config_id, user_id)
    if not cfg or not cfg.is_active:
        return False
    if cfg.is_protected:
        return False

    cfg.is_active = False

    sub = await get_user_subscription(session, user_id)
    if sub:
        sub.active_configs_count = await get_active_configs_count(session, user_id)

    await session.commit()
    return True


async def set_config_paid_until(
    session: AsyncSession, config_id: int, paid_until: datetime
) -> None:
    await session.execute(
        update(ClientConfig).where(ClientConfig.id == config_id).values(paid_until=paid_until)
    )
    await session.commit()


async def extend_all_configs_paid_until(session: AsyncSession, user_id: int, days: int) -> int:
    """Сдвинуть срок оплаты всех активных конфигов. Возвращает число затронутых."""
    configs = await get_user_configs(session, user_id)
    for cfg in configs:
        if cfg.paid_until and cfg.paid_until > utcnow():
            cfg.paid_until += timedelta(days=days)
        else:
            cfg.paid_until = utcnow() + timedelta(days=days)
    await session.commit()
    return len(configs)


async def get_expired_configs(session: AsyncSession) -> list[ClientConfig]:
    """Активные конфиги, у которых истекла оплата."""
    stmt = select(ClientConfig).where(
        ClientConfig.is_active.is_(True),
        ClientConfig.paid_until.isnot(None),
        ClientConfig.paid_until < utcnow(),
    )
    return list((await session.execute(stmt)).scalars().all())
