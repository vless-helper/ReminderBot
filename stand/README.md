# Тестовый стенд

Бот + PostgreSQL + настоящий `cli-vless-manager` + `sing-box` + reloader.

## Что проверяет

- Бот поднимается, создаёт схему в PostgreSQL, видит админку по сети.
- `bot.db` работает на реальном Postgres, а не только на SQLite в тестах.
- Оплата → конфиг в БД **и** в админке;VLESS-ссылка совпадает.
- Истечение подписки → конфиги архивируются в админке, пользователь получает уведомление.
- Продление из замороженного состояния → конфиги разблокируются.
- Два плательщика не видят конфиги и платежи друг друга.

## Запуск

Нужны клоны двух соседних репозиториев:

```bash
git clone https://github.com/vless-helper/cli-vless-manager.git
git clone https://github.com/vless-helper/docker-container-reloader.git
```

```bash
# 1. Стендовые ключи (реальные сюда класть нельзя)
docker run --rm ghcr.io/sagernet/sing-box:latest generate reality-keypair
docker run --rm ghcr.io/sagernet/sing-box:latest generate rand --hex 8

cp stand/manager.env.example stand/manager.env   # вписать public key
cp stand/config.example.json stand/config.json   # вписать private key и short_id
echo '[]' > stand/clients.json

# 2. Токен бота в .env.stand заведомо невалидный
cp .env.example .env.stand

export VLESS_MANAGER_SRC=$PWD/../cli-vless-manager
export RELOADER_SRC=$PWD/../docker-container-reloader

BOT_ENV_FILE=.env.stand POSTGRES_PASSWORD=standpass \
  docker compose -f docker-compose.yml -f docker-compose.stand.yml up -d --build
```

## Ожидаемое поведение

| Контейнер | Состояние |
|---|---|
| `reminder-bot-db` | `healthy` |
| `reminder-stand-vless-manager` | `healthy` |
| `reminder-stand-sing-box` | `Up` |
| `reminder-stand-reloader` | `Up` |
| `reminder-bot` | `Restarting` — **так и задумано** |

Бот перезапускается, потому что в `.env.stand` заведомо невалидный
`BOT_TOKEN`. Это удобный признак: развернулось всё, кроме авторизации в
Telegram. С настоящим токеном бот выйдет из этого цикла.

## Интеграционные тесты

```bash
export VLESS_MANAGER_SRC=$PWD/../cli-vless-manager
export RELOADER_SRC=$PWD/../docker-container-reloader
BOT_ENV_FILE=.env.stand POSTGRES_PASSWORD=standpass \
docker compose -f docker-compose.yml -f docker-compose.stand.yml \
  run --rm --no-deps -T --entrypoint sh \
  -v "$PWD/tests:/app/tests:ro" -v "$PWD/pytest.ini:/app/pytest.ini:ro" \
  bot -c "pip install -q pytest pytest-asyncio aiosqlite && \
    TEST_LIVE_DB='postgresql+asyncpg://postgres:standpass@db:5432/reminderbot' \
    TEST_LIVE_API='http://vless-manager:8080' \
    BOT_TOKEN=1:A ADMIN_IDS=1 python -m pytest tests/test_live_integration.py -v"
```

## Сброс состояния админки

Админка хранит состояние в двух файлах (`stand/config.json` и
`stand/clients.json`) без транзакции между ними. Если они
рассинхронизировались — сбросьте оба:

```bash
echo '[]' > stand/clients.json
# обнулить inbound.users в stand/config.json
docker compose -f docker-compose.yml -f docker-compose.stand.yml \
  restart vless-manager sing-box
```

Известная проблема `cli-vless-manager` (не бота): имя пользователя не
уникально. Если reload релоадера упал, запись остаётся в `clients.json`,
и следующий `create_user` с тем же именем создаёт **дубль** с другим UUID.
Бот от этого защищён (`UNIQUE` на `config_name` и `(user_id, config_number)`),
но в админке мусор копится — чистить нужно руками.

## Важно про безопасность

`reloader` монтирует `/var/run/docker.sock` — это фактически root-доступ к
хосту. В бою монтируйте сокет только туда, где это действительно нужно.

Ключи в `stand/manager.env` и `stand/config.json` — стендовые, сгенерированы
специально для локального прогона. Боевые ключи сюда класть нельзя.
