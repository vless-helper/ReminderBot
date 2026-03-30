from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload 
from sqlalchemy import select, update, and_
from typing import Optional, List

from .models import User, Subscription, Payment

async def extend_subscription(
    session: AsyncSession, 
    user_id: int, 
    days: int = 30
) -> Subscription:
    """Продлить подписку"""
    subscription = await get_user_subscription(session, user_id)
    
    if subscription:
        # Если есть активная подписка, продлеваем
        if subscription.next_payment > datetime.now():
            subscription.next_payment += timedelta(days=days)
        else:
            # Если подписка истекла, начинаем с сегодня
            subscription.next_payment = datetime.now() + timedelta(days=days)
        subscription.status = "active"
        subscription.period_days = days
    else:
        # Создаем новую подписку
        subscription = Subscription(
            user_id=user_id,
            next_payment=datetime.now() + timedelta(days=days),
            status="active",
            period_days=days
        )
        session.add(subscription)
    
    await session.commit()
    await session.refresh(subscription)
    return subscription

async def get_last_pending_payment(session: AsyncSession, user_id: int) -> Optional[Payment]:
    """Получить последний необработанный платеж пользователя"""
    from sqlalchemy import desc
    
    stmt = select(Payment).where(
        and_(
            Payment.user_id == user_id,
            Payment.status == "pending"
        )
    ).order_by(desc(Payment.created_at)).limit(1)
    
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_or_create_user(
    session: AsyncSession, 
    telegram_id: int, 
    username: Optional[str] = None
) -> User:
    """Получить пользователя или создать нового"""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        user = User(
            telegram_id=telegram_id,
            username=username
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    
    return user

async def update_payment_status(session: AsyncSession, payment_id: int, status: str):
    """Обновить статус платежа"""
    stmt = update(Payment).where(Payment.id == payment_id).values(status=status)
    await session.execute(stmt)
    await session.commit()

async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Получить пользователя по telegram_id"""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_subscription(session: AsyncSession, user_id: int) -> Optional[Subscription]:
    """Получить подписку пользователя"""
    stmt = select(Subscription).where(Subscription.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def check_subscription_status(session: AsyncSession, user_id: int) -> bool:
    """Проверить активна ли подписка"""
    subscription = await get_user_subscription(session, user_id)
    if subscription and subscription.status == "active":
        return subscription.next_payment > datetime.now()
    return False


async def create_payment(
    session: AsyncSession, 
    user_id: int, 
    status: str = "pending"
) -> Payment:
    """Создать запись о платеже"""
    payment = Payment(
        user_id=user_id,
        status=status
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


#Напоминалки 

async def get_expiring_subscriptions(
    session: AsyncSession, 
    days_before: int = 3
) -> List[Subscription]:
    """Получить подписки, которые истекают через указанное количество дней"""
    target_date = datetime.now() + timedelta(days=days_before)
    target_date_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    target_date_end = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    stmt = select(Subscription).options(
        selectinload(Subscription.user)
    ).where(
        and_(
            Subscription.status == "active",
            Subscription.next_payment.between(target_date_start, target_date_end),
            Subscription.last_reminder_sent.is_(None)
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_expiring_tomorrow_subscriptions(session: AsyncSession) -> List[Subscription]:
    """Получить подписки, которые истекают завтра"""
    tomorrow = datetime.now().date() + timedelta(days=1)
    start_of_tomorrow = datetime.combine(tomorrow, datetime.min.time())
    end_of_tomorrow = datetime.combine(tomorrow, datetime.max.time())
    
    stmt = select(Subscription).options(
        selectinload(Subscription.user)
    ).where(
        and_(
            Subscription.status == "active",
            Subscription.next_payment.between(start_of_tomorrow, end_of_tomorrow),
            Subscription.last_reminder_sent.is_(None)  # не отправляли напоминание
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_expired_today_subscriptions(session: AsyncSession) -> List[Subscription]:
    """Получить подписки, истекшие сегодня"""
    today = datetime.now().date()
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = datetime.combine(today, datetime.max.time())
    
    stmt = select(Subscription).options(
        selectinload(Subscription.user)
    ).where(
        and_(
            Subscription.status == "active",
            Subscription.next_payment.between(start_of_day, end_of_day)
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def mark_reminder_sent(session: AsyncSession, subscription_id: int):
    """Отметить, что напоминание отправлено"""
    stmt = (
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(last_reminder_sent=datetime.now())
    )
    await session.execute(stmt)
    await session.commit()


async def reset_reminder_flag(session: AsyncSession, subscription_id: int):
    """Сбросить флаг напоминания (для тестирования)"""
    stmt = (
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(last_reminder_sent=None)
    )
    await session.execute(stmt)
    await session.commit()