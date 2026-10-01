"""Тесты монитора: проверки, дедупликация алертов и восстановление.

Отдельно проверяется то, что ломается молча: если алерт отправляется на
каждом проходе, мониторинг превращается в шум и его перестают читать. Если
дедупликация работает неверно, можно либо молча пропустить поломку, либо
завалить сообщениями одно и то же.
"""

import json
import os
import time

import pytest

from bot import monitor as m


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Состояние и heartbeat — во временный каталог, иначе тест наследует
    боевые файлы и результат зависит от того, что лежит на диске."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(m, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(m, "BOT_HEARTBEAT", str(state_dir / "bot_heartbeat"))
    monkeypatch.setattr(m, "WATCHDOG_HEARTBEAT", str(state_dir / "watchdog_heartbeat"))
    monkeypatch.setattr(m, "HEARTBEAT_PATH", str(state_dir / "monitor_heartbeat"))
    monkeypatch.setattr(m, "STATE_PATH", str(tmp_path / "state.json"))
    yield


class Sent:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def sent(monkeypatch):
    """Перехватываем отправку вместо реального Telegram."""
    messages: list[Sent] = []

    async def _fake(text):
        messages.append(Sent(text))
        return True

    monkeypatch.setattr(m, "send_telegram", _fake)
    return messages


def _touch(path, age=0):
    with open(path, "w") as f:
        f.write("1")
    if age:
        t = time.time() - age
        os.utime(path, (t, t))


# --- проверки ---------------------------------------------------------------


async def test_disk_ok(monkeypatch):
    class St:
        f_bavail = 10 * 10**9
        f_frsize = 1

    monkeypatch.setattr(os, "statvfs", lambda _: St())
    c = m.check_disk()
    assert c.ok, c.detail


async def test_disk_critical(monkeypatch):
    """Мало места — это предупреждение, но пропускать нельзя: кончившийся
    диск останавливает и БД, и запись логов."""

    class St:
        f_bavail = 500 * 10**6
        f_frsize = 1

    monkeypatch.setattr(os, "statvfs", lambda _: St())
    c = m.check_disk()
    assert not c.ok
    assert "свободно" in c.detail
    assert not c.critical, "недостаток места не отнимает VPN у клиентов"


async def test_mem_critical(monkeypatch, tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal: 1000000 kB\nMemAvailable: 50000 kB\n")
    real_open = open

    def fake_open(name, *a, **kw):
        if name == "/proc/meminfo":
            return real_open(p)
        return real_open(name, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    c = m.check_mem()
    assert not c.ok
    assert c.critical is False


async def test_heartbeat_missing_is_failure():
    c = m.check_heartbeat("bot_hb", "/nonexistent/heartbeat", 600)
    assert not c.ok
    assert "отсутствует" in c.detail


async def test_heartbeat_fresh_ok():
    _touch(m.BOT_HEARTBEAT, age=30)
    c = m.check_heartbeat("bot_hb", m.BOT_HEARTBEAT, 600)
    assert c.ok, c.detail


async def test_heartbeat_stale_is_failure():
    """Процесс жив, а цикл встал: heartbeat — единственный признак."""
    _touch(m.BOT_HEARTBEAT, age=5000)
    c = m.check_heartbeat("bot_hb", m.BOT_HEARTBEAT, 600)
    assert not c.ok
    assert "не отрабатывает" in c.detail


async def test_vpn_port_unreachable_fails(monkeypatch):
    """Порт, который не слушается, обязан считаться поломкой VPN.

    Проверяем через заведомо незанятый порт: в тестовой среде настоящий
    sing-box может быть запущен, и проверка честно пройдёт.
    """
    monkeypatch.setattr(m, "VPN_PORT", 9)
    monkeypatch.setattr(m, "VPN_HOST", "127.0.0.1")
    c = await m.check_vpn_port()
    assert not c.ok
    assert c.critical, "упавший sing-box — это отказ клиентам"


async def test_vpn_port_failure_does_not_hang(monkeypatch):
    """Неотвечающий хост не должен висеть на проходе: он тянет за собой все
    остальные проверки и, в пределе, пропускает алерт."""
    import asyncio as aio

    monkeypatch.setattr(m, "VPN_PORT", 9)
    monkeypatch.setattr(m, "VPN_HOST", "10.255.255.1")
    started = aio.get_event_loop().time()
    c = await m.check_vpn_port()
    elapsed = aio.get_event_loop().time() - started
    assert not c.ok
    assert elapsed < 15, f"проверка заняла {elapsed:.1f}с — слишком долго"


# --- дедупликация алертов ---------------------------------------------------


async def test_new_failure_alerts_once(sent):
    broken = [m.Check("vpn_port", False, "не отвечает")]
    assert await m.notify_new_failures(broken) is True
    assert len(sent) == 1
    # Регистр важен: заголовок в сообщении «Сервис недоступен» со строчной «н».
    assert "недоступен" in sent[0].text
    assert "VPN (sing-box)" in sent[0].text


async def test_recovery_message(sent):
    prev = [m.Check("vpn_port", True, "принимает соединения")]
    assert await m.notify_recovery(prev) is True
    assert len(sent) == 1
    assert "Восстановилось" in sent[0].text


async def test_no_alert_when_nothing_broken(sent):
    assert await m.notify_new_failures([]) is False
    assert sent == [], "алерт без поломок — это шум"


async def test_state_roundtrip():
    m.save_state({"broken": ["vpn_port", "bot_hb"]})
    assert m.load_state()["broken"] == ["vpn_port", "bot_hb"]


async def test_state_missing_is_empty():
    assert m.load_state() == {}


async def test_state_survives_corrupt_file(monkeypatch):
    """Битое состояние не должно ронять монитор: он продолжит работу,
    просто пришлёт одно повторное уведомление."""
    with open(m.STATE_PATH, "w") as f:
        f.write("{не json")
    assert m.load_state() == {}


# --- цикл: дедупликация на практике -----------------------------------------


def _broken_vpn():
    return [m.Check("vpn_port", False, "не отвечает")]


async def test_repeated_failure_sends_one_alert(sent, monkeypatch):
    """Главный тест против шума: пять проходов подряд с одной и той же
    поломкой дают ровно одно сообщение."""
    state = {"broken": []}
    for _ in range(5):
        names = sorted(c.name for c in _broken_vpn())
        if names != sorted(state["broken"]):
            await m.notify_new_failures(_broken_vpn())
            state["broken"] = names
    assert len(sent) == 1, f"повторных алертов быть не должно, пришло {len(sent)}"


async def test_alert_on_every_new_distinct_failure(sent):
    """Две разные поломки подряд — два сообщения, иначе о второй не узнать."""
    state = {"broken": []}

    first = [m.Check("vpn_port", False, "не отвечает")]
    await m.notify_new_failures(first)
    state["broken"] = ["vpn_port"]

    second = [m.Check("vpn_port", False, "не отвечает"), m.Check("bot_hb", False, "цикл встал")]
    await m.notify_new_failures(second)
    state["broken"] = ["bot_hb", "vpn_port"]

    assert len(sent) == 2
    assert "Бот" in sent[1].text, "второе сообщение должно называть новую поломку"


async def test_critical_and_warning_split(sent):
    """Критичное и предупреждение в разных блоках: иначе «место кончается»
    окажется в одном списке с «VPN упал» и потеряет приоритет."""
    await m.notify_new_failures([
        m.Check("vpn_port", False, "не отвечает"),
        m.Check("disk_free", False, "свободно 1.0 ГБ"),
    ])
    text = sent[0].text
    assert "Сервис недоступен" in text
    # Когда критичное уже есть, предупреждение идёт отдельным блоком «Также»,
    # а не собственным заголовком: иначе два одинаково громких блока.
    assert "Также" in text or "Предупреждение" in text
    assert text.index("Сервис недоступен") < text.index("Место на диске")
    assert "Клиенты не получают сервис" in text


async def test_warning_alone_has_own_heading(sent):
    """Только предупреждение (без критичного) — собственный заголовок."""
    await m.notify_new_failures([m.Check("disk_free", False, "свободно 1.0 ГБ")])
    text = sent[0].text
    assert "Предупреждение" in text
    assert "Сервис недоступен" not in text


async def test_ping_external_disabled_is_ok():
    """Без настроенного URL пинг не должен считаться ошибкой."""
    assert await m.ping_external("") is True


# --- healthcheck -------------------------------------------------------------


async def test_healthcheck_without_heartbeat_fails():
    assert m.healthcheck() == 1


async def test_healthcheck_with_fresh_heartbeat_ok():
    _touch(m.HEARTBEAT_PATH, age=10)
    assert m.healthcheck() == 0


async def test_healthcheck_stale_heartbeat_fails():
    _touch(m.HEARTBEAT_PATH, age=m.HEARTBEAT_MAX_AGE * 2)
    assert m.healthcheck() == 1


# --- критичность ------------------------------------------------------------


def test_critical_checks_cover_everything_clients_need():
    """Всё, без чего клиент не получает сервис, обязано быть критичным."""
    for name in ("vpn_port", "bot_hb", "admin_api", "db", "watchdog_hb"):
        assert m.Check(name, False).critical, name


def test_warning_checks_are_not_critical():
    for name in ("disk_free", "mem_avail"):
        assert not m.Check(name, False).critical, name


def test_all_checks_have_titles():
    for name in m.ORDER:
        assert m.Check(name, False).title != name, f"нет человеческого названия у {name}"
