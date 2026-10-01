#!/usr/bin/env bash
# Резервная копия ReminderBot: база платежей + конфигурация sing-box.
#
# Хранится 14 последних копий. Копии с других серверов нет: при потере
# этого VPS бэкап исчезнет вместе с ним.

set -euo pipefail

DEPLOY_DIR="$HOME/reminderbot"
BACKUP_DIR="$DEPLOY_DIR/backups"
KEEP=14
STAMP=$(date +%Y%m%d-%H%M%S)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$BACKUP_DIR"

# 1. Дамп базы. pg_dump -Fc даёт сжатый архив, который восстанавливается
#    через pg_restore и не ломается на непустых таблицах, в отличие от
#    серии INSERT.
docker exec reminder-bot-db pg_dump -U postgres -d reminderbot -Fc > "$WORK/db.dump"

# 2. Конфигурация sing-box: приватный ключ Reality, список клиентов.
#    Без неё выданные конфиги перестанут работать после переустановки.
cp "$DEPLOY_DIR/config/config.json" "$WORK/config.json"
cp "$DEPLOY_DIR/config/clients.json" "$WORK/clients.json"

# 3. Секреты бота и менеджера, чтобы развернуть стек заново без простоя.
cp "$DEPLOY_DIR/.env" "$WORK/bot.env"
cp "$DEPLOY_DIR/manager.env" "$WORK/manager.env"
cp "$DEPLOY_DIR/.secrets" "$WORK/secrets.env"
cp "$DEPLOY_DIR/docker-compose.yml" "$WORK/docker-compose.yml"

# Проверяем, что дамп не пустой и читается: битый бэкап хуже отсутствия,
# о нём узнаёшь только в момент аварии.
if ! docker run --rm -v "$WORK:/b:ro" postgres:16-alpine \
     pg_restore --list /b/db.dump >/dev/null 2>&1; then
  echo "ОШИБКА: дамп БД не читается — копия не создана" >&2
  exit 1
fi

# 4. Исходники. Копии с других серверов нет, поэтому потеря этого VPS
#    означала бы потерю кода целиком. Секреты сюда не попадают: в app/ и
#    manager/ их нет, .env и .secrets копируются отдельно (п. 3).
tar czf "$WORK/app-src.tar.gz" -C "$DEPLOY_DIR" \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude='*.pyc' \
    --exclude=.git --exclude=bot.db app 2>/dev/null || true
tar czf "$WORK/manager-src.tar.gz" -C "$DEPLOY_DIR" \
    --exclude=.git --exclude='*.log' manager 2>/dev/null || true

# Исходник, из которого собран запущенный образ: без этого нельзя понять,
# что именно работает, если развернули не последний коммит.
COMMIT=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
         reminder-bot 2>/dev/null || true)
echo "${COMMIT:-unknown}" > "$WORK/deployed-revision.txt"

tar czf "$BACKUP_DIR/backup-$STAMP.tar.gz" -C "$WORK" .
rm -rf "$WORK"

chmod 600 "$BACKUP_DIR/backup-$STAMP.tar.gz"

# Ротация
ls -1t "$BACKUP_DIR"/backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

SIZE=$(du -h "$BACKUP_DIR/backup-$STAMP.tar.gz" | cut -f1)
COUNT=$(ls -1 "$BACKUP_DIR"/backup-*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
echo "Бэкап создан: backup-$STAMP.tar.gz ($SIZE), всего копий: $COUNT"
