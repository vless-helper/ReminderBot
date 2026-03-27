# bot/reminder.py
import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.config import config
from bot.db import crud

logger = logging.getLogger(__name__)


async def check_and_send_reminders(bot: Bot):
    """Проверка и отправка напоминаний"""
    engine = create_async_engine(config.DATABASE_URL)
    async_session_maker = async_sessionmaker(engine)
    
    async with async_session_maker() as session:
        # Подписки, которые истекают через 3 дня
        expiring_soon = await crud.get_expiring_subscriptions(session, config.REMINDER_DAYS_BEFORE)
        
        for subscription in expiring_soon:
            user = subscription.user
            days_left = (subscription.next_payment - datetime.now()).days
            
            try:
                await bot.send_message(
                    user.telegram_id,
                    f"⚠️ Напоминание!\n\n"
                    f"Ваша подписка истекает через {days_left} дня(ей).\n"
                    f"Пожалуйста, продлите подписку, чтобы не потерять доступ.\n\n"
                    f"Для продления нажмите /start"
                )
                
                # Отмечаем, что напоминание отправлено
                await crud.mark_reminder_sent(session, subscription.id)
                
            except Exception as e:
                logger.error(f"Ошибка отправки напоминания пользователю {user.telegram_id}: {e}")
        
        # Подписки, которые истекли сегодня
        today = datetime.now().date()
        expired_today = await crud.get_expired_today_subscriptions(session, today)
        
        for subscription in expired_today:
            user = subscription.user
            
            try:
                await bot.send_message(
                    user.telegram_id,
                    f"⏰ Внимание!\n\n"
                    f"Срок вашей подписки истек!\n"
                    f"Пожалуйста, продлите подписку для продолжения использования сервиса.\n\n"
                    f"Для продления нажмите /start"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления об истечении пользователю {user.telegram_id}: {e}")


async def run_reminder_loop():
    """Запуск цикла проверки напоминаний"""
    bot = Bot(token=config.BOT_TOKEN)
    
    while True:
        try:
            await check_and_send_reminders(bot)
        except Exception as e:
            logger.error(f"Ошибка в цикле напоминаний: {e}")
        
        # Проверяем раз в час
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(run_reminder_loop())