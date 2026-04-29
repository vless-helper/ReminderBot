import asyncio
import logging
from datetime import datetime
from aiogram import Bot
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.api.client import admin_api
from bot.config import config
from bot.db import crud

logger = logging.getLogger(__name__)


def should_send_reminder() -> bool:
    """Проверяет, нужно ли отправлять напоминания сейчас"""
    current_hour = datetime.now().hour

    if hasattr(config, 'REMINDER_START_HOUR') and hasattr(config, 'REMINDER_END_HOUR'):
        return config.REMINDER_START_HOUR <= current_hour <= config.REMINDER_END_HOUR

    # Если ничего не задано, отправляем всегда
    return True

async def send_reminder_3days(bot: Bot, subscription, days_left: int):
    """Отправить напоминание за 3 дня"""
    try:
        await bot.send_message(
            subscription.user.telegram_id,
            f"⚠️ Напоминание!\n\n"
            f"Ваша подписка истекает через {days_left + 1} дня(ей).\n"
            f"Дата блокировки: {subscription.next_payment.strftime('%d.%m.%Y')}\n\n"
            f"Пожалуйста, продлите подписку до этого времени, чтобы не потерять доступ.\n\n"
            f"Для продления нажмите 'Продлить подписку'"
        )
        logger.info(f"Напоминание за {days_left} дня отправлено пользователю {subscription.user.telegram_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания: {e}")
        return False


async def send_reminder_1day(bot: Bot, subscription):
    """Отправить напоминание за 1 день"""
    try:
        expire_time = subscription.next_payment
        expire_time_str = expire_time.strftime('%d.%m.%Y в %H:%M')
        
        await bot.send_message(
            subscription.user.telegram_id,
            f"⚠️ Срочное напоминание!\n\n"
            f"ЗАВТРА ({expire_time_str}) ваша подписка истекает!\n\n"
            f"Пожалуйста, продлите подписку сегодня, чтобы не потерять доступ.\n\n"
            f"Для продления нажмите 'Продлить подписку'"
        )
        logger.info(f"Напоминание за 1 день отправлено пользователю {subscription.user.telegram_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания за 1 день: {e}")
        return False


async def send_expired_today_reminder(bot: Bot, subscription, session):
    """Отправить уведомление, что подписка истекла сегодня"""
    try:
        await bot.send_message(
            subscription.user.telegram_id,
            f"⏰ Внимание!\n\n"
            f"Ваша подписка была заморожена!\n"
            f"Оплатите подписку для продолжения использования сервиса.\n\n"
            f"Для оплаты нажмите 'Купить подписку'"
        )
        logger.info(f"Уведомление об истечении отправлено пользователю {subscription.user.telegram_id}")



        # Исправлено: меняем статус через session, а не через subscription.session
        subscription.status = "expired"
        await session.commit()

        configs = await crud.get_user_configs(session, subscription.user_id)
        archived_configs = 0

        for config in configs:
            success = await admin_api.archive_user(config.config_name)
            if success:
                archived_configs += 1
                logger.info(f"Заархивированно {archived_configs} конфигов пользователя {subscription.user.telegram_id}")

        return True
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об истечении: {e}")
        return False


async def check_and_send_reminders(bot: Bot):
    """Проверка и отправка напоминаний"""

    if not should_send_reminder():
        logger.debug("Сейчас не время для отправки напоминаний")
        return
    
    engine = create_async_engine(config.DATABASE_URL, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_maker() as session:
        # 1. Напоминания за 3 дня до истечения
        expiring_soon = await crud.get_expiring_subscriptions(session, 3)
        
        for subscription in expiring_soon:
            days_left = (subscription.next_payment - datetime.now()).days
            
            # subscription.user уже загружен через selectinload
            success = await send_reminder_3days(bot, subscription, days_left)
            
            if success:
                await crud.mark_reminder_sent(session, subscription.id)
        
        # 2. Напоминания за 1 день до истечения (ДОБАВЛЯЕМ!)
        expiring_tomorrow = await crud.get_expiring_tomorrow_subscriptions(session)
        
        for subscription in expiring_tomorrow:
            success = await send_reminder_1day(bot, subscription)
            
            if success:
                await crud.mark_reminder_sent(session, subscription.id)
        
        # 3. Уведомления, что подписка истекла сегодня + архивирование конфигов
        expired_today = await crud.get_expired_today_subscriptions(session)
        
        for subscription in expired_today:
            # subscription.user уже загружен через selectinload
            await send_expired_today_reminder(bot, subscription, session)
    
    await engine.dispose()


async def reminder_loop():
    """Запуск цикла проверки напоминаний"""
    bot = Bot(token=config.BOT_TOKEN)
    
    logger.info("Цикл напоминаний запущен")
    
    while True:
        try:
            await check_and_send_reminders(bot)
        except Exception as e:
            logger.error(f"Ошибка в цикле напоминаний: {e}")
        
        # Для теста 10 секунд, для продакшена 3600
        await asyncio.sleep(10)  # В продакшене поменять на 3600


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(reminder_loop())