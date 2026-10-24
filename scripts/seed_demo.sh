#!/usr/bin/env bash
# Синтетические данные для проверки отчётов hr-analytics. Запуск из любого места, нужен доступ к docker.
#   ./scripts/seed_demo.sh demo      # письма на демо-данных БЕЗ записи в базы (проще всего, ничего не нужно чистить)
#   ./scripts/seed_demo.sh up        # залить демо-пользователей в Postgres и статистику/события в Mongo
#   ./scripts/seed_demo.sh preview   # собрать отчёты по данным из баз и сохранить HTML+PDF (письма не шлются)
#   ./scripts/seed_demo.sh send      # отправить отчёты по данным из баз
#   ./scripts/seed_demo.sh down      # удалить демо-пользователей и их статистику
# Все письма уходят на один адрес (REPORT_TO, по умолчанию j.mereskin@yandex.com) с пометкой [TEST]:
# реальным получателям ничего не отправляется, даже если у них есть email.
# Для send/demo в .env должны быть SMTP_USER и SMTP_PASSWORD (пароль приложения Яндекса).
# Демо-данные: uid 10000000-0000-0000-0000-… (HR, hr_head, директора) и 20000000-0000-0000-0000-… (новички),
# max_id 920000000–920000016, email …@example.com. Пока "up" не сделан "down", эти люди видны реальному HR в отчётах.
set -eu
cd "$(dirname "$0")/.."

REPORT_TO=${REPORT_TO:-j.mereskin@yandex.com}
MODE=${1:-}

env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }
[ -f .env ] || { echo "нет .env в $(pwd)"; exit 2; }

if ! docker info >/dev/null 2>&1; then
  echo "Нет доступа к Docker. Запустите: sudo ./scripts/seed_demo.sh $MODE"
  exit 2
fi

need_smtp() {
  if [ -z "$(env_val SMTP_USER)" ] || [ -z "$(env_val SMTP_PASSWORD)" ]; then
    echo "В .env не заданы SMTP_USER / SMTP_PASSWORD (пароль приложения Яндекса): письма отправить нельзя."
    echo "Сохранить отчёты без отправки: ./scripts/seed_demo.sh preview"
    exit 2
  fi
}

# сервис hr-analytics из профиля reports; mock/ монтируем: в образ он не входит
ha() { docker compose --profile reports run --rm --no-deps -v "$PWD/hr_analytics/mock:/srv/mock:ro" "$@"; }
mongo_js() {
  docker compose exec -T mongo mongosh --quiet -u "$(env_val MONGO_APP_USER)" -p "$(env_val MONGO_APP_PASSWORD)" \
    --authenticationDatabase "$(env_val MONGO_DB)" "$(env_val MONGO_DB)" --eval "$1"
}

case "$MODE" in
  demo)
    need_smtp
    ha hr-analytics python -m app send --demo --to "$REPORT_TO"
    ;;
  up)
    docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < hr_analytics/mock/seed_users.sql
    ha hr-analytics python mock/seed_mongo.py
    echo "Демо-данные залиты. Дальше: preview / send. Убрать: ./scripts/seed_demo.sh down"
    ;;
  preview)
    mkdir -p hr_analytics/reports
    ha -v "$PWD/hr_analytics/reports:/srv/reports" hr-analytics python -m app preview
    echo "Отчёты: hr_analytics/reports/ (HTML и PDF)"
    ;;
  send)
    need_smtp
    ha hr-analytics python -m app send --to "$REPORT_TO"
    ;;
  down)
    # сначала новички (director_id ссылается на руководителей с RESTRICT), потом остальные
    docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
DELETE FROM users WHERE id::text LIKE '20000000-0000-0000-0000-%' AND max_id BETWEEN 920000000 AND 920000016;
DELETE FROM users WHERE id::text LIKE '10000000-0000-0000-0000-%' AND max_id BETWEEN 920000000 AND 920000016;
SQL
    mongo_js "const re = /^(10|20)000000-0000-0000-0000-/; printjson({stats: db.getCollection('stats').deleteMany({uid: re}).deletedCount, events: db.getCollection('events').deleteMany({uid: re}).deletedCount})"
    echo "Демо-данные удалены."
    ;;
  *)
    sed -n 2,13p "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
