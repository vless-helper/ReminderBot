"""Гашение просроченных подписок без Telegram и без процесса бота.

Раньше истечение обрабатывал только reminder-цикл внутри бота. Упадёт
контейнер — просрочка перестанет отсваиваться, и клиенты продолжат
пользоваться VPN. Этот сторож работает отдельно: ему нужны только база
и админка, поэтому отказоустойчивость бота на блокировку не влияет.

    python -m bot.expiry_watchdog
"""

import asyncio
import logging
import os
import signal
import sys

from bot.api.client import admin_api, AdminAPIError
from bot.config import config, utcnow
from bot.db import crud
from bot.db.base import create_engine_and_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("expiry_watchdog")

CHECK_INTERVAL = int(os.environ.get("WATCHDOG_INTERVAL", "300"))

# Heartbeat-файл: Docker проверяет его свежесть, а не наличие процесса.
# Так ловится и смерть процесса, и цикл, который перестал делать проходы
# (процесс жив, но истёкшие не гаснут — худший вид тихой поломки).
HEARTBEAT_PATH = os.environ.get("WATCHDOG_HEARTBEAT", "/tmp/watchdog_heartbeat")
HEARTBEAT_MAX_AGE = CHECK_INTERVAL * 2 + 60


def touch_heartbeat() -> None:
    try:
        with open(HEARTBEAT_PATH, "w") as f:
            f.write(str(int(utcnow().timestamp())))
    except OSError as e:  # pragma: no cover
        logger.warning("Не удалось обновить heartbeat: %s", e)


def healthcheck() -> int:
    """Код 0 — сторож жив и недавно проходил цикл."""
    import time

    try:
        age = time.time() - os.path.getmtime(HEARTBEAT_PATH)
    except OSError:
        print("heartbeat отсутствует — сторож ни разу не отработал цикл")
        return 1
    if age > HEARTBEAT_MAX_AGE:
        print(f"heartbeat устарел на {int(age)}с (предел {HEARTBEAT_MAX_AGE}с)")
        return 1
    print(f"ok, последний проход {int(age)}с назад")
    return 0


async def expire_once(session_maker) -> tuple[int, int]:
    """Один проход. Возвращает (истекших подписок, заблокированных конфигов)."""
    subs_done = 0
    configs_blocked = 0

    async with session_maker() as session:
        subs = await crud.get_expired_subscriptions(session)

        for sub in subs:
            configs = await crud.get_user_configs(session, sub.user_id)
            blocked = 0
            failed: list[str] = []

            for cfg in configs:
                if not cfg.is_active:
                    continue
                try:
                    await admin_api.archive_user(cfg.config_name)
                    blocked += 1
                except AdminAPIError as e:
                    # Не сдвигаем статус подписки: если хоть один конфиг не
                    # отозван, доступ клиенту ещё есть, и молчаливая запись
                    # «истекло» скроет дыру. Повторим на следующем проходе.
                    failed.append(cfg.config_name)
                    logger.error("Не удалось заблокировать %s: %s", cfg.config_name, e)

            if failed:
                logger.error(
                    "Подписка клиента %s: заблокировано %d из %d, не удалось: %s. Повторю.",
                    sub.user.telegram_id, blocked, len(configs), ", ".join(failed),
                )
                continue

            await crud.expire_subscription(session, sub.id)
            subs_done += 1
            configs_blocked += blocked
            logger.info(
                "Подписка клиента %s истекла, заблокировано конфигов: %d",
                sub.user.telegram_id, blocked,
            )

    return subs_done, configs_blocked


async def main() -> int:
    engine, session_maker = create_engine_and_session(config.DATABASE_URL)
    touch_heartbeat()

    if not await admin_api.health_check():
        logger.error(
            "Админка недоступна по %s — сторож не может блокировать. "
            "Блокировка не сработает, пока менеджер лежит.", config.ADMIN_API_URL
        )

    logger.info(
        "Сторож просрочки запущен: интервал %ds, БД %s, админка %s",
        CHECK_INTERVAL, config.DATABASE_URL.split("@")[-1], config.ADMIN_API_URL,
    )

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass

    try:
        while not stop.is_set():
            try:
                subs, configs = await expire_once(session_maker)
                touch_heartbeat()
                if subs:
                    logger.info("Проход завершён: подписок %d, конфигов %d", subs, configs)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                # Не обновляем heartbeat: если цикл падает каждый раз,
                # Docker должен показать unhealthy, а не тихо помечать зелёным.
                logger.exception("Ошибка в проходе: %s", e)

            try:
                await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL)
            except asyncio.TimeoutError:
                continue
    finally:
        await admin_api.close()
        await engine.dispose()
        logger.info("Сторож остановлен")

    return 0


if __name__ == "__main__":
    if "--health" in sys.argv:
        raise SystemExit(healthcheck())
    raise SystemExit(asyncio.run(main()))
