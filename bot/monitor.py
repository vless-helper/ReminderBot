"""Монитор живости: проверяет сервисы и пишет админу, только когда что-то сломалось.

Зачем это отдельный сервис, а не ещё одна проверка в expiry_watchdog:

  * expiry_watchdog отвечает за блокировку просроченных. Там уже есть цикл с
    heartbeat, и подмешивать туда ещё и проверку VPN смешало бы две разные
    ответственности: поломка монитора гасила бы просрочку и наоборот.
  * этому сервису Telegram нужен только чтобы отправить алерт. Он не
    подписывается на апдейты и не держит второе подключение long polling —
    сообщения он шлёт напрямую через Bot API. Поэтому падение самого бота
    (оборван long polling, завис dp.start_polling) не мешает доставить
    сообщение о поломке.

Что проверяем и почему именно это:

  db          БД — без неё не работает ни бот, ни сторож. SELECT 1.
  bot_hb      Heartbeat ботового цикла. Ловит худший вид поломки: процесс
              жив, порт слушает, healthcheck зелёный, но цикл перестал
              делать проходы. Наличие процесса такую поломку не видит.
  watchdog_hb То же для сторожа просрочки.
  admin_api   Менеджер. Без него бот не может ни выдать конфиг, ни заблокировать
              просроченного, но формально бот при этом «жив».
  vpn_port    TCP-соединение к sing-box. Ловит упавший VPN.
  disk_free   Место на хосте: кончится — встанет и БД, и логи.
  mem_avail   Память: OOM убивает контейнеры по очереди, и это выглядит
              как необъяснимые перезапуски.

Про VPN и сеть. Проверка vpn_port идёт по внутренней сети Docker, поэтому
она НЕ видит, что сломалось на хосте: файрвол, провайдер или публикация
порта. Снаружи 443 открыт, но изнутри мы проверим только то, что сервис
принимает соединения. Открытый порт на хосте проверяет внешний сторож
(healthchecks.io) — оба слоя нужны, каждый ловит то, чего не видит другой.
"""

import asyncio
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import aiohttp

from bot.config import config, utcnow
from bot.db.base import create_engine_and_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("monitor")

CHECK_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "120"))
TELEGRAM_TIMEOUT = int(os.environ.get("MONITOR_TELEGRAM_TIMEOUT", "15"))

# Heartbeat-файлы лежат в общем томе state, а не в /tmp: бот, сторож и монитор
# — разные контейнеры, и каждый со своим /tmp. В локальном запуске без тома
# путь остаётся рабочим, поэтому проверки не отличаются от боевых.
STATE_DIR = os.environ.get("MONITOR_STATE_DIR", "/state")
HEARTBEAT_PATH = os.environ.get("MONITOR_HEARTBEAT", f"{STATE_DIR}/monitor_heartbeat")
HEARTBEAT_MAX_AGE = CHECK_INTERVAL * 2 + 60

BOT_HEARTBEAT = os.environ.get("MONITOR_BOT_HEARTBEAT", f"{STATE_DIR}/bot_heartbeat")
WATCHDOG_HEARTBEAT = os.environ.get(
    "MONITOR_WATCHDOG_HEARTBEAT", f"{STATE_DIR}/watchdog_heartbeat"
)

VPN_HOST = os.environ.get("MONITOR_VPN_HOST", "sing-box")
VPN_PORT = int(os.environ.get("MONITOR_VPN_PORT", "443"))

# Пороги. Диск важнее памяти: кончившийся диск останавливает всё сразу и
# без возможности записать логи причины.
DISK_MIN_FREE_GB = float(os.environ.get("MONITOR_DISK_MIN_FREE_GB", "2"))
MEM_MIN_AVAIL_MB = int(os.environ.get("MONITOR_MEM_MIN_AVAIL_MB", "128"))

# Хранилище состояния нужно между проходами: без него «упало -> восстановилось
# -> упало» за последние сутки даст три сообщения, и алерт станет шумом.
STATE_PATH = os.environ.get("MONITOR_STATE", "/tmp/monitor_state.json")


@dataclass
class Check:
    """Результат одной проверки."""

    name: str
    ok: bool
    detail: str = ""

    @property
    def title(self) -> str:
        return {
            "db": "База данных",
            "bot_hb": "Бот",
            "watchdog_hb": "Сторож просрочки",
            "admin_api": "Менеджер конфигов",
            "vpn_port": "VPN (sing-box)",
            "disk_free": "Место на диске",
            "mem_avail": "Свободная память",
        }.get(self.name, self.name)

    @property
    def critical(self) -> bool:
        """Критичное — клиенты не получают сервис. Остальное предупреждение."""
        return self.name in {"db", "bot_hb", "watchdog_hb", "admin_api", "vpn_port"}


# Порядок в отчёте: от самого важного к менее важному, чтобы в сообщении
# первым оказалось то, что чинить в первую очередь.
ORDER = ["vpn_port", "bot_hb", "admin_api", "db", "watchdog_hb", "disk_free", "mem_avail"]


def _age(path: str) -> Optional[float]:
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def check_db(session_maker) -> Check:
    from sqlalchemy import text

    async def _run() -> Check:
        try:
            async with session_maker() as session:
                await session.execute(text("SELECT 1"))
            return Check("db", True)
        except Exception as e:  # noqa: BLE001
            return Check("db", False, str(e)[:200])

    return _run()  # type: ignore[return-value]


def check_heartbeat(name: str, path: str, max_age: float) -> Check:
    age = _age(path)
    if age is None:
        return Check(name, False, "heartbeat отсутствует — сервис ни разу не отработал цикл")
    if age > max_age:
        return Check(name, False, f"цикл не отрабатывает {int(age)}с (предел {int(max_age)}с)")
    return Check(name, True, f"последний проход {int(age)}с назад")


def check_admin_api() -> Awaitable[Check]:
    from bot.api.client import admin_api

    async def _run() -> Check:
        try:
            ok = await admin_api.health_check()
            return Check("admin_api", ok, "" if ok else f"нет ответа от {config.ADMIN_API_URL}")
        except Exception as e:  # noqa: BLE001
            return Check("admin_api", False, str(e)[:200])

    return _run()


def check_vpn_port() -> Check:
    async def _run() -> Check:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(VPN_HOST, VPN_PORT), timeout=8
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return Check("vpn_port", True, f"{VPN_HOST}:{VPN_PORT} принимает соединения")
        except Exception as e:  # noqa: BLE001
            return Check("vpn_port", False, f"{VPN_HOST}:{VPN_PORT} не отвечает: {e}"[:200])

    return _run()  # type: ignore[return-value]


def check_disk() -> Check:
    try:
        st = os.statvfs("/")
        free_gb = st.f_bavail * st.f_frsize / 1e9
        if free_gb < DISK_MIN_FREE_GB:
            return Check("disk_free", False, f"свободно {free_gb:.1f} ГБ, нужно минимум {DISK_MIN_FREE_GB} ГБ")
        return Check("disk_free", True, f"свободно {free_gb:.1f} ГБ")
    except OSError as e:
        return Check("disk_free", False, f"не удалось узнать место: {e}")


def check_mem() -> Check:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mb = int(line.split()[1]) // 1024
                    if mb < MEM_MIN_AVAIL_MB:
                        return Check("mem_avail", False, f"доступно {mb} МБ, нужно минимум {MEM_MIN_AVAIL_MB} МБ")
                    return Check("mem_avail", True, f"доступно {mb} МБ")
        return Check("mem_avail", False, "в /proc/meminfo нет MemAvailable")
    except (OSError, ValueError) as e:
        return Check("mem_avail", False, f"не удалось прочитать память: {e}")


async def run_checks(session_maker) -> list[Check]:
    """Все проверки за один проход. Проверка не должна ронять цикл: сбой самой
    проверки считаем провалом этой проверки, а не молчаливый успех."""
    results = await asyncio.gather(
        check_db(session_maker),
        check_admin_api(),
        check_vpn_port(),
        return_exceptions=True,
    )
    checks: list[Check] = []
    for name, res in zip(("db", "admin_api", "vpn_port"), results):
        if isinstance(res, BaseException):
            checks.append(Check(name, False, f"проверка упала: {res}"[:200]))
        else:
            checks.append(res)

    # Предел для heartbeat бота берём от его собственного интервала: цикл
    # напоминаний ходит раз в REMINDER_CHECK_INTERVAL, и задержка в пару
    # таких интервалов — ещё не поломка.
    checks.append(
        check_heartbeat("bot_hb", BOT_HEARTBEAT, config.REMINDER_CHECK_INTERVAL * 2 + 120)
    )
    checks.append(check_heartbeat("watchdog_hb", WATCHDOG_HEARTBEAT, HEARTBEAT_MAX_AGE))
    checks.append(check_disk())
    checks.append(check_mem())
    checks.sort(key=lambda c: ORDER.index(c.name) if c.name in ORDER else 99)
    return checks


# --- отправка алертов -------------------------------------------------------


def _fmt(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "✅" if c.ok else "❌"
        detail = f" — {c.detail}" if c.detail else ""
        lines.append(f"{mark} {c.title}{detail}")
    return "\n".join(lines)


def _stamp() -> str:
    return config.local(utcnow()).strftime("%Y-%m-%d %H:%M")


async def send_telegram(text: str) -> bool:
    """Отправляет всем админам. Возвращает False, если не ушло ни одному.

    Bot API вместо aiogram: подписка на апдейты тут не нужна, а лишнее
    long polling-соединение только мешало бы боту при неполадках в сети.
    """
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
    delivered = False
    timeout = aiohttp.ClientTimeout(total=TELEGRAM_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for admin_id in config.ADMIN_IDS:
            try:
                async with session.post(
                    url, json={"chat_id": admin_id, "text": text, "disable_web_page_preview": True}
                ) as resp:
                    body = await resp.json()
                if body.get("ok"):
                    delivered = True
                else:
                    logger.error("Telegram отклонил сообщение: %s", body.get("description"))
            except Exception as e:  # noqa: BLE001
                logger.error("Не удалось отправить алерт админу %s: %s", admin_id, e)
    return delivered


async def notify_new_failures(broken: list[Check]) -> bool:
    """Сообщение о новых поломках. Возвращает True, если есть что слать."""
    critical = [c for c in broken if c.critical]
    warnings = [c for c in broken if not c.critical]
    if not broken:
        return False

    parts = []
    if critical:
        parts.append(
            "🚨 <b>Сервис недоступен</b>\n\n"
            + _fmt(critical)
            + "\n\n_Клиенты не получают сервис._"
        )
    if warnings:
        head = "⚠️ <b>Предупреждение</b>" if not critical else "⚠️ <b>Также</b>"
        parts.append(head + "\n\n" + _fmt(warnings) + "\n\n_Пока работает, но требует внимания._")
    text = "\n\n".join(parts) + f"\n\n_{_stamp()}_"
    return await send_telegram(text)


async def notify_recovery(previous: list[Check]) -> bool:
    """Сообщение о восстановлении — иначе неизвестно, чинили минуту или сутки."""
    text = (
        "✅ <b>Восстановилось</b>\n\n"
        + _fmt(previous)
        + "\n\n_Было сломано, теперь работает._"
        + f"\n\n_{_stamp()}_"
    )
    return await send_telegram(text)


# --- состояние между проходами ---------------------------------------------


def load_state() -> dict:
    import json

    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    import json

    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        logger.warning("Не удалось сохранить состояние: %s", e)


def touch_heartbeat() -> None:
    try:
        with open(HEARTBEAT_PATH, "w") as f:
            f.write(str(int(time.time())))
    except OSError as e:  # pragma: no cover
        logger.warning("Не удалось обновить heartbeat: %s", e)


def healthcheck() -> int:
    try:
        age = time.time() - os.path.getmtime(HEARTBEAT_PATH)
    except OSError:
        print("heartbeat отсутствует — монитор ни разу не отработал проход")
        return 1
    if age > HEARTBEAT_MAX_AGE:
        print(f"heartbeat устарел на {int(age)}с (предел {HEARTBEAT_MAX_AGE}с)")
        return 1
    print(f"ok, последний проход {int(age)}с назад")
    return 0


async def ping_external(url: str) -> bool:
    """Пинг внешнего сторожа (healthchecks.io).

    Зеркало нашего прохода наружу: он должен видеть и наши успехи, иначе
    не отличит «всё хорошо» от «мы умерли и не смогли сообщить».
    """
    if not url:
        return True
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status < 400
    except Exception as e:  # noqa: BLE001
        logger.error("Внешний сторож недоступен (%s): %s", url, e)
        return False


async def main() -> int:
    engine, session_maker = create_engine_and_session(config.DATABASE_URL)
    touch_heartbeat()
    state = load_state()
    prev_broken = state.get("broken", [])

    logger.info(
        "Монитор запущен: интервал %ds, проверки %s",
        CHECK_INTERVAL, ", ".join(ORDER),
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
                checks = await run_checks(session_maker)
                broken = [c for c in checks if not c.ok]
                broken_names = sorted(c.name for c in broken)
                prev_names = sorted(prev_broken)

                if broken_names != prev_names:
                    newly = [c for c in broken if c.name not in prev_names]
                    healed = [c for c in checks if c.ok and c.name in prev_names]
                    if newly:
                        logger.error(
                            "Сломалось: %s", ", ".join(c.name for c in newly)
                        )
                        await notify_new_failures(broken)
                    if healed:
                        logger.info(
                            "Восстановилось: %s", ", ".join(c.name for c in healed)
                        )
                        await notify_recovery(healed)

                    state["broken"] = broken_names
                    save_state(state)
                    prev_broken = broken_names

                if broken:
                    logger.warning("Проблемы: %s", ", ".join(broken_names))
                else:
                    logger.info("Все проверки пройдены")

                if await ping_external(os.environ.get("MONITOR_PING_URL", "")):
                    touch_heartbeat()

            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                # Heartbeat намеренно не обновляем: если проход падает каждый
                # раз, Docker должен показать unhealthy, а не тихо зелёный.
                logger.exception("Ошибка в проходе: %s", e)

            try:
                await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL)
            except asyncio.TimeoutError:
                continue
    finally:
        await engine.dispose()
        logger.info("Монитор остановлен")

    return 0


if __name__ == "__main__":
    if "--health" in sys.argv:
        raise SystemExit(healthcheck())
    raise SystemExit(asyncio.run(main()))
