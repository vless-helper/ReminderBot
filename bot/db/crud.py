from sqlalchemy import select, update, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload 
from datetime import datetime, timedelta
from typing import Optional, List
from bot.db.models import ClientConfig
from bot.config import config

from .models import User, Subscription, Payment

# Обработка подписок

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

async def extend_subscription_months(
    session: AsyncSession, 
    user_id: int, 
    months: int
) -> Subscription:
    """Продлить подписку на несколько месяцев"""
    days = months * 30  
    
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

# Обработка платежей

async def get_last_pending_payment(session: AsyncSession, user_id: int) -> Optional[Payment]:
    """Получить последний необработанный платеж пользователя"""
    
    stmt = select(Payment).where(
        and_(
            Payment.user_id == user_id,
            Payment.status == "pending"
        )
    ).order_by(desc(Payment.created_at)).limit(1)
    
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def update_payment_status(session: AsyncSession, payment_id: int, status: str):
    """Обновить статус платежа"""
    stmt = update(Payment).where(Payment.id == payment_id).values(status=status)
    await session.execute(stmt)
    await session.commit()

async def create_payment(
    session: AsyncSession, 
    user_id: int, 
    status: str = "pending",
    amount: Optional[int] = None,
    months: Optional[int] = None
) -> Payment:
    """Создать запись о платеже"""
    payment = Payment(
        user_id=user_id,
        status=status,
        amount=amount,
        months=months
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment

# Обработка пользователей

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

async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Получить пользователя по telegram_id"""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

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

# Операции с доп. конфигами 

async def get_user_configs(session: AsyncSession, user_id: int) -> List[ClientConfig]:
    """Получить все активные конфиги пользователя"""
    
    stmt = select(ClientConfig).where(
        ClientConfig.user_id == user_id,
        ClientConfig.is_active == True
    ).order_by(ClientConfig.config_number)
    
    result = await session.execute(stmt)
    configs = result.scalars().all()
    print(f"DEBUG get_user_configs: user_id={user_id}, найдено={len(configs)}")
    for cfg in configs:
        print(f"  - id={cfg.id}, number={cfg.config_number}")
    return configs

async def get_active_configs_count(session: AsyncSession, user_id: int) -> int:
    """Получить количество активных конфигов пользователя"""
    configs = await get_user_configs(session, user_id)
    return len(configs)

async def calculate_monthly_price(session: AsyncSession, user_id: int) -> int:
    """Рассчитать месячную стоимость подписки (базовая цена * количество конфигов)"""
    configs_count = await get_active_configs_count(session, user_id)
    base_price = config.BASE_PRICE  # 150₽ за конфиг
    total_price = base_price * configs_count
    
    # Можно добавить скидку за количество конфигов (опционально)
    if configs_count >= 10:
        total_price = int(total_price * 0.9)  # 10% скидка от 10 конфигов
    
    return total_price

# async def create_client_config_with_payment(
#     session: AsyncSession, 
#     user_id: int, 
#     config_number: int,
#     config_name: str,
#     vless_link: str,
#     subscription_id: int,
#     paid_until: datetime
# ) -> ClientConfig:
#     """Создать новый конфиг с привязкой к подписке"""
#     config = ClientConfig(
#         user_id=user_id,
#         config_number=config_number,
#         config_name=config_name,
#         vless_link=vless_link,
#         is_active=True,
#         subscription_id=subscription_id,
#         paid_until=paid_until
#     )
#     session.add(config)
#     await session.commit()
#     await session.refresh(config)
    
#     # Обновляем количество активных конфигов в подписке
#     subscription = await get_user_subscription(session, user_id)
#     if subscription:
#         subscription.active_configs_count = await get_active_configs_count(session, user_id)
#         await session.commit()
    
#     return config


# async def update_subscription_price(session: AsyncSession, user_id: int):
#     """Обновить стоимость подписки на основе количества конфигов"""
#     subscription = await get_user_subscription(session, user_id)
#     if subscription:
#         configs_count = await get_active_configs_count(session, user_id)
#         subscription.active_configs_count = configs_count
#         await session.commit()


async def get_next_config_number(session: AsyncSession, user_id: int) -> int:
    """Получить следующий номер конфига для пользователя"""
    stmt = select(ClientConfig).where(
        ClientConfig.user_id == user_id,
        ClientConfig.is_active == True
    )
    result = await session.execute(stmt)
    configs = result.scalars().all()
    
    if not configs:
        return 1
    else:
        return max(c.config_number for c in configs) + 1

async def create_client_config(
    session: AsyncSession, 
    user_id: int, 
    config_number: int,
    config_name: str,
    vless_link: str,
    is_protected: bool = False
) -> ClientConfig:
    """Создать новый конфиг для пользователя"""
    config = ClientConfig(
        user_id=user_id,
        config_number=config_number,
        config_name=config_name,
        vless_link=vless_link,
        is_active=True,
        is_protected=is_protected  # флаг защиты
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config

async def deactivate_config(session: AsyncSession, config_id: int, user_id: int) -> bool:
    """Деактивировать конфиг (нельзя удалить защищенный конфиг)"""
    # Проверяем, что конфиг принадлежит пользователю и не защищен
    stmt = select(ClientConfig).where(
        ClientConfig.id == config_id,
        ClientConfig.user_id == user_id,
        ClientConfig.is_active == True,
        ClientConfig.is_protected == False  # Защищенные нельзя удалить
    )
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    
    if not config:
        return False
    
    # Деактивируем конфиг
    config.is_active = False
    await session.commit()
    
    # Обновляем количество активных конфигов в подписке
    subscription = await get_user_subscription(session, user_id)
    if subscription:
        active_count = await get_active_configs_count(session, user_id)
        subscription.active_configs_count = active_count
        await session.commit()
    
    return True

async def delete_config_from_admin(session: AsyncSession, config_name: str) -> bool:
    """Удалить конфиг из админки (без проверок)"""
    from bot.api.client import admin_api
    return await admin_api.delete_user(config_name)
