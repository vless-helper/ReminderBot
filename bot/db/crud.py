from sqlalchemy import select, update, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload 
from datetime import datetime, timedelta
from typing import Optional, List
from bot.config import config

from bot.api.client import admin_api
from bot.db.models import User, Subscription, Payment, ClientConfig

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

from datetime import datetime, timedelta

async def get_remaining_days_until_next_payment(session: AsyncSession, user_id: int) -> int:
    """
    Получает количество дней до следующего платежа
    """
    subscription = await get_user_subscription(session, user_id)
    if not subscription or subscription.next_payment <= datetime.now():
        return 0
    
    days_left = (subscription.next_payment - datetime.now()).days + 1
    return max(0, days_left)


async def calculate_prorated_price(session: AsyncSession, user_id: int, base_price: int) -> int:
    """
    Рассчитывает пропорциональную цену за остаток месяца
    """
    days_left = await get_remaining_days_until_next_payment(session, user_id)
    
    if days_left <= 0:
        return base_price
    
    # Стандартный месяц = 30 дней
    standard_month = 30
    ratio = days_left / standard_month
    
    # Пропорциональная цена (округляем вниз до рублей)
    prorated_price = int(base_price * ratio)
    
    # Минимальная цена - 1 рубль (чтобы не было бесплатно)
    return max(1, prorated_price)

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
    """Получить подписки, истекшие сегодня в конкретное время"""
    now = datetime.now()
    
    stmt = select(Subscription).options(
        selectinload(Subscription.user)
    ).where(
        and_(
            Subscription.status == "active",
            Subscription.next_payment <= now
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()

# async def get_expired_today_subscriptions(session: AsyncSession) -> List[Subscription]:
#     """Получить подписки, истекшие сегодня"""
#     today = datetime.now().date()
#     start_of_day = datetime.combine(today, datetime.min.time())
#     end_of_day = datetime.combine(today, datetime.max.time())
    
#     stmt = select(Subscription).options(
#         selectinload(Subscription.user)
#     ).where(
#         and_(
#             Subscription.status == "active",
#             Subscription.next_payment.between(start_of_day, end_of_day)
#         )
#     )
#     result = await session.execute(stmt)
#     return result.scalars().all()

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

# Обработка конфигов

async def get_expired_configs(session: AsyncSession) -> List[ClientConfig]:
    """
    Получить все конфиги, у которых истек срок оплаты
    """
    now = datetime.now()
    stmt = select(ClientConfig).where(
        ClientConfig.is_active == True,
        ClientConfig.paid_until < now,
    )
    result = await session.execute(stmt)
    return result.scalars().all()

async def archive_expired_configs(session: AsyncSession) -> List[dict]:
    """
    Архивация просроченных конфигов
    Возвращает список словарей с информацией об архивированных конфигах для отправки в админку
    """
    expired_congigs = await get_expired_configs(session)
    archived_info = []

    for config in expired_congigs:
        success = await archive_config_in_admin(config.config_name)

        if success:
            config.is_active = False
            archived_info.append({
                'config_id': config.id,
                'config_number': config.config_number,
                'user_id': config.user_id, 
                'config_name': config.config_name,
                'telegram_id': None  # Заполним позже, когда загрузим пользователя
            })

        await session.commit()
        return archived_info
    
async def archive_config_in_admin(config_name: str) -> bool:
    """
    Отправляет PATCH запрос в админку для архивации конфига
    """
    return admin_api.archive_user(config_name)

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
    
    if total_price == 0:
        total_price = base_price 

    return total_price

async def extend_all_configs_paid_until(session: AsyncSession, user_id: int, months: int):
    """
    Продлевает paid_until для всех активных конфигов пользователя
    """
    configs = await get_user_configs(session, user_id)
    days_to_add = months * 30
    
    for config in configs:
        if config.paid_until:
            config.paid_until += timedelta(days=days_to_add)
        else:
            config.paid_until = datetime.now() + timedelta(days=days_to_add)
    
    await session.commit()

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
    is_protected: bool = False,
    paid_until: Optional[datetime] = None 
) -> ClientConfig:
    """Создать новый конфиг для пользователя"""
    config = ClientConfig(
        user_id=user_id,
        config_number=config_number,
        config_name=config_name,
        vless_link=vless_link,
        is_active=True,
        is_protected=is_protected,
        paid_until=paid_until  
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
