"""Служебный скрипт: посмотреть подписки и подвинуть дату заморозки вручную.

    python update_payment_date.py                      # список всех пользователей
    python update_payment_date.py 123456789 1          # заморозка через 1 день
    python update_payment_date.py 123456789 -1         # сделать просроченным
"""

import asyncio
import sys
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from bot.config import config, utcnow
from bot.db import crud
from bot.db.base import create_engine_and_session
from bot.db.models import ReminderLog, User


async def update_payment_date(session, telegram_id: int, days_from_now: int) -> None:
    result = await session.execute(
        select(User)
        .options(selectinload(User.subscription))
        .where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        print(f"❌ Пользователь с telegram_id={telegram_id} не найден")
        return
    if not user.subscription:
        print(f"❌ У пользователя нет подписки")
        return

    new_date = utcnow() + timedelta(days=days_from_now)
    user.subscription.next_payment = new_date
    await session.commit()

    # Срок изменился — отметки о напоминаниях больше неактуальны
    await session.execute(
        delete(ReminderLog).where(ReminderLog.subscription_id == user.subscription.id)
    )
    await session.commit()

    print(f"✅ Обновлено для пользователя @{user.username or telegram_id}")
    print(f"   Новая дата платежа: {new_date:%d.%m.%Y %H:%M} UTC")
    print(f"   Через дней: {days_from_now}")


async def list_all_users(session) -> None:
    result = await session.execute(
        select(User)
        .options(selectinload(User.subscription))
        .order_by(User.created_at.desc())
    )
    users = result.scalars().all()

    print("\n📋 Все пользователи:")
    print("=" * 78)
    for user in users:
        sub = user.subscription
        print(f"   ID: {user.telegram_id} | @{user.username or '—'} | {sub.status if sub else '❌ нет подписки'}")
        if sub:
            days_left = (sub.next_payment - utcnow()).days
            print(f"      заморозка {config.format_dt(sub.next_payment)} UTC | дней: {days_left} | конфигов: {sub.active_configs_count}")
    print("=" * 78)


async def main() -> None:
    engine, session_maker = create_engine_and_session(config.DATABASE_URL)
    try:
        async with session_maker() as session:
            if len(sys.argv) > 1:
                await update_payment_date(session, int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 1)
            else:
                await list_all_users(session)
    finally:
        await engine.dispose()

    print("\n📝 Использование:")
    print("  python update_payment_date.py                        — список пользователей")
    print("  python update_payment_date.py TELEGRAM_ID ДНЕЙ       — задать заморозку")
    print("  Пример: python update_payment_date.py 123456789 1     (завтра)")
    print("  Пример: python update_payment_date.py 123456789 0     (сегодня)")
    print("  Пример: python update_payment_date.py 123456789 -1    (просрочена)")


if __name__ == "__main__":
    asyncio.run(main())
