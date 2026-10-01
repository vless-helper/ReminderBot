"""Напоминания об оплате.

Раньше был один флаг last_reminder_sent на все виды напоминаний, поэтому
3-дневное гасило 1-дневное, а 1-дневного не существовало вовсе, если
REMINDER_DAYS_BEFORE содержал только 3. Теперь отметки лежат в reminder_log
по ключу (подписка, дней_до), поэтому окна независимы и добавляются через
REMINDER_DAYS_BEFORE без правок кода.

Также раньше был отдельный контейнер `python -m bot.reminder`, поднимавший
второе подключение к Telegram. Теперь цикл живёт в основном процессе.
"""

import asyncio
import logging

from aiogram import Bot

from bot.api.client import AdminAPIError, admin_api
from bot.config import config, utcnow
from bot.db import crud
from bot.keyboards.keyboards import get_extend_keyboard
from bot.utils.helpers import format_price
from bot.utils.pricing import monthly_price

logger = logging.getLogger(__name__)


def in_reminder_window() -> bool:
    """Отправляем напоминания только в заданные часы по локальному времени."""
    hour = config.local(utcnow()).hour
    return config.REMINDER_START_HOUR <= hour <= config.REMINDER_END_HOUR


def _plural_days(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "дней"
    last = n % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


async def _send_upcoming(bot: Bot, sub, days_before: int, configs_count: int) -> bool:
    """Напоминание «скоро заморозка». Текст зависит от числа дней."""
    days_left = max(0, (sub.next_payment - utcnow()).days)

    if days_before == 1:
        header = "⚠️ <b>Завтра заморозка подписки</b>"
        body = "После заморозки доступ к конфигам будет закрыт."
    elif days_before <= 3:
        header = "⏳ <b>Скоро заморозка подписки</b>"
        body = "Продлите заранее — и конфиги продолжат работать."
    else:
        header = f"⏳ <b>До заморозки подписки {days_left} дн.</b>"
        body = "Продлите заранее, чтобы не потерять доступ к конфигам."

    try:
        await bot.send_message(
            sub.user.telegram_id,
            f"{header}\n\n"
            f"Дата заморозки: {config.format_dt(sub.next_payment)}\n\n"
            f"{body}\n"
            f"Выберите срок продления ниже.",
            reply_markup=get_extend_keyboard(configs_count),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Не удалось отправить напоминание (-%s дн.) пользователю %s: %s",
            days_before,
            sub.user.telegram_id,
            e,
        )
        return False


async def _expire(bot: Bot, session, sub) -> bool:
    """Сообщить об истечении и отозвать доступ к конфигам в админке."""
    try:
        configs_count = await crud.get_active_configs_count(session, sub.user_id)
        price = monthly_price(configs_count)

        await bot.send_message(
            sub.user.telegram_id,
            f"🔒 <b>Подписка заморожена</b>\n\n"
            f"Срок истёк {config.format_dt(sub.next_payment, with_time=True)}.\n"
            f"Доступ к конфигам отключён.\n\n"
            f"Чтобы вернуть доступ, оплатите {format_price(price)} — "
            f"кнопка «Продлить подписку».",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Не удалось отправить уведомление об истечении %s: %s", sub.user.telegram_id, e
        )

    configs = await crud.get_user_configs(session, sub.user_id)
    blocked = 0
    for cfg in configs:
        try:
            await admin_api.archive_user(cfg.config_name)
            blocked += 1
        except AdminAPIError as e:
            # Пользователь заблокирован не полностью — сообщаем админу
            logger.error("Не удалось заблокировать %s: %s", cfg.config_name, e)
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"❌ Не удалось заблокировать конфиг {cfg.config_name} "
                        f"(пользователь {sub.user.telegram_id}): {e}",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Не удалось отправить алерт админу %s", admin_id)

    await crud.expire_subscription(session, sub.id)
    logger.info(
        "Подписка %s истекла: заблокировано конфигов %d/%d",
        sub.user.telegram_id,
        blocked,
        len(configs),
    )
    return True


async def check_and_send_reminders(bot: Bot, session_maker=None) -> int:
    """Один проход проверки. Возвращает количество отправленных уведомлений.

    Истечение обрабатывается вне окна напоминаний: иначе просрочку можно было бы
    пропустить, если цикл упал ночью.
    """
    if session_maker is None:
        from bot.db.base import create_engine_and_session

        engine, session_maker = create_engine_and_session(config.DATABASE_URL)
        owns_engine = True
    else:
        engine = None
        owns_engine = False

    sent = 0

    try:
        async with session_maker() as session:
            if in_reminder_window():
                for days_before in sorted(config.REMINDER_DAYS_BEFORE, reverse=True):
                    for sub in await crud.get_due_reminder_subscriptions(session, days_before):
                        configs_count = await crud.get_active_configs_count(session, sub.user_id)
                        if await _send_upcoming(bot, sub, days_before, configs_count):
                            await crud.mark_reminder_sent(session, sub.id, days_before)
                            sent += 1

            for sub in await crud.get_expired_subscriptions(session):
                if await _expire(bot, session, sub):
                    sent += 1
    finally:
        if owns_engine:
            await engine.dispose()

    if sent:
        logger.info("Отправлено уведомлений: %d", sent)
    return sent


async def reminder_loop(bot: Bot, session_maker):
    """Фоновый цикл. Запускается вместе с ботом в том же процессе."""
    logger.info(
        "Цикл напоминаний запущен: интервал %ds, окно %02d–%02d %s, окна напоминаний: %s",
        config.REMINDER_CHECK_INTERVAL,
        config.REMINDER_START_HOUR,
        config.REMINDER_END_HOUR,
        config.REMINDER_TIMEZONE,
        config.REMINDER_DAYS_BEFORE,
    )

    while True:
        try:
            await check_and_send_reminders(bot, session_maker)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Ошибка в цикле напоминаний: %s", e)

        await asyncio.sleep(config.REMINDER_CHECK_INTERVAL)
