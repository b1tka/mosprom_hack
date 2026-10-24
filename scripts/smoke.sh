#!/usr/bin/env bash
# Смоук-проверка стека после деплоя. Запуск на VPS из корня проекта:
#   ./scripts/smoke.sh              # всё, включая проверки с LLM (нужен запущенный ollama и модель)
#   ./scripts/smoke.sh --no-llm     # без проверок, которым нужна модель
#   ./scripts/smoke.sh --keep       # не удалять тестовые данные (uid 00000000-0000-4000-8000-0000000005a0)
# FAIL — стек неисправен, WARN — требует внимания (SMTP не настроен, LLM ошиблась в оценке), код выхода 1 только при FAIL.
# Тестовый пользователь: записи в Mongo stats/events с этим uid; после проверки удаляются. В Postgres скрипт не пишет.
set -u
cd "$(dirname "$0")/.."

NO_LLM=0; KEEP=0
for a in "$@"; do
  case "$a" in
    --no-llm) NO_LLM=1 ;;
    --keep) KEEP=1 ;;
    *) echo "неизвестный аргумент: $a"; exit 2 ;;
  esac
done

env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }
[ -f .env ] || { echo "нет .env в $(pwd)"; exit 2; }

ML_PORT=$(env_val ML_PORT); ML_PORT=${ML_PORT:-8000}
ML="http://localhost:${ML_PORT}"
MONGO_USER=$(env_val MONGO_APP_USER); MONGO_PASS=$(env_val MONGO_APP_PASSWORD); MONGO_DB=$(env_val MONGO_DB)
TOPIC=$(env_val KAFKA_TOPIC_ANSWERS); TOPIC=${TOPIC:-onboarding.answers}
UID_T="00000000-0000-4000-8000-0000000005a0"

PASS=0; FAILS=0; WARNS=0
ok()   { PASS=$((PASS+1)); printf '  \033[32mOK\033[0m    %s\n' "$1"; }
fail() { FAILS=$((FAILS+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
warn() { WARNS=$((WARNS+1)); printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
section() { printf '\n== %s\n' "$1"; }

dc() { docker compose "$@"; }
curl_ml() { curl --noproxy '*' -sS --max-time "${CURL_TIMEOUT:-20}" "$@"; }
pg() { dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$0"' "$1" 2>&1; }
mongo_js() {
  dc exec -T mongo mongosh --quiet -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase "$MONGO_DB" "$MONGO_DB" --eval "$1" 2>&1
}

# без доступа к docker.sock все проверки ниже дали бы ложные FAIL
if ! docker info >/dev/null 2>&1; then
  echo "Нет доступа к Docker: $(docker info 2>&1 | grep -m1 -i 'permission denied\|cannot connect' | cut -c1-120)"
  echo "Запустите через sudo:  sudo ./scripts/smoke.sh"
  echo "или добавьте себя в группу:  sudo usermod -aG docker \$USER  (затем перелогиниться)"
  exit 2
fi

cleanup() {
  [ "$KEEP" = 1 ] && return
  mongo_js "db.getCollection('stats').deleteMany({uid:'$UID_T'}); db.getCollection('events').deleteMany({uid:'$UID_T'}); db.getCollection('locks').deleteMany({_id:'stats:$UID_T'})" >/dev/null
}
trap cleanup EXIT
cleanup   # остатки прошлого запуска

# ---------------------------------------------------------------------------
section "Контейнеры"
echo "  ядро: $(uname -r)"
for svc in postgres mongo redis s3 kafka ml ml-mood-consumer bot; do
  state=$(dc ps -a --format '{{.Service}}|{{.State}}|{{.Health}}' 2>/dev/null | grep "^$svc|" | head -1)
  if [ -z "$state" ]; then fail "$svc: контейнера нет (docker compose up -d?)"; continue; fi
  run=$(echo "$state" | cut -d'|' -f2); health=$(echo "$state" | cut -d'|' -f3)
  if [ "$run" != "running" ]; then fail "$svc: $run"
  elif [ -n "$health" ] && [ "$health" != "healthy" ]; then fail "$svc: running, но health=$health"
  else ok "$svc: running${health:+, $health}"; fi
done
if dc ps -a --format '{{.Service}}|{{.State}}' | grep -q '^ollama|running'; then ok "ollama: running"
else
  if [ "$NO_LLM" = 1 ]; then echo "  --    ollama не проверяется (--no-llm)"
  else warn "ollama не запущен: проверки с LLM будут пропущены или упадут (docker compose --profile llm up -d)"; fi
fi

# ---------------------------------------------------------------------------
section "Postgres: схема"
for q in \
  "users.hr_id|select 1 from information_schema.columns where table_name='users' and column_name='hr_id'" \
  "users.email|select 1 from information_schema.columns where table_name='users' and column_name='email'" \
  "users.phone|select 1 from information_schema.columns where table_name='users' and column_name='phone'" \
  "таблица invites|select 1 from information_schema.tables where table_name='invites'"; do
  name=${q%%|*}; sql=${q#*|}
  [ "$(pg "$sql")" = "1" ] && ok "$name" || fail "$name отсутствует: применить db/migrations/*.sql (до пересборки бота)"
done
roles=$(pg "select string_agg(name, ',' order by name) from role")
echo "  роли: $roles"
hr_count=$(pg "select count(*) from users u join role r on r.id=u.role_id where r.name in ('hr','hr_head')")
[ "${hr_count:-0}" -ge 1 ] 2>/dev/null && ok "HR-пользователей: $hr_count" || fail "нет ни одного hr/hr_head в users (бот не покажет HR меню)"
noemail=$(pg "select count(*) from users u join role r on r.id=u.role_id where r.name in ('hr','director','hr_head') and coalesce(u.email,'')=''")
[ "${noemail:-0}" = "0" ] && ok "у HR/руководителей заполнен email" || warn "без email: $noemail (отчёты им не уйдут, UPDATE users SET email=…)"

# ---------------------------------------------------------------------------
section "Mongo"
ver=$(mongo_js "print(db.version())" | tail -1)
case "$ver" in
  7.*) ok "версия MongoDB $ver" ;;
  [0-9]*) warn "версия MongoDB $ver: на ядре 6.19–7.0.13 mongod 8.x не стартует, ожидается 7.x" ;;
  *) fail "mongosh не отвечает: $ver" ;;
esac
w=$(mongo_js "db.getCollection('smoke_probe').insertOne({_id:'p'}).acknowledged; db.getCollection('smoke_probe').drop()" | head -1)
[ "$w" = "true" ] && ok "запись в базу $MONGO_DB под пользователем приложения" || fail "запись в Mongo не удалась: $w"

# ---------------------------------------------------------------------------
section "Redis / Kafka / S3"
[ "$(dc exec -T redis redis-cli ping 2>&1 | tr -d '\r')" = "PONG" ] && ok "redis PONG" || fail "redis не отвечает"
TOPIC_ALERTS=$(env_val KAFKA_TOPIC_ALERTS); TOPIC_ALERTS=${TOPIC_ALERTS:-onboarding.alerts}
topics=$(dc exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list 2>&1)
for t in "$TOPIC" "$TOPIC_ALERTS"; do
  echo "$topics" | grep -qx "$t" && ok "топик $t существует" || fail "топика $t нет (docker compose up -d kafka-init)"
done
s3code=$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:$(env_val S3_API_PORT)/minio/health/live" 2>/dev/null)
[ "$s3code" = "200" ] && ok "s3 live" || warn "s3 health-эндпоинт вернул '${s3code:-нет ответа}' (проверьте вручную, если порт не проброшен)"

# ---------------------------------------------------------------------------
section "ML: API"
# ml при старте грузит эмбеддинги (до нескольких минут): порт уже проброшен, а ответа ещё нет
for i in $(seq 1 24); do
  h=$(curl_ml "$ML/health" 2>&1)
  echo "$h" | grep -q '"ok"' && break
  [ "$i" = 1 ] && echo "  ждём готовности ml (до 2 минут)…"
  sleep 5
done
echo "$h" | grep -q '"ok"' && ok "GET /health" || fail "GET /health: $h"
docs=$(curl_ml "$ML/v1/documents" 2>&1)
n=$(echo "$docs" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["documents"]))' 2>/dev/null)
if [ -z "$n" ]; then fail "GET /v1/documents: $(echo "$docs" | cut -c1-120)"
elif [ "$n" = "0" ]; then warn "база знаний пуста: /v1/ask будет отвечать 503 (python -m app.ingest ...)"
else ok "база знаний: документов $n"; fi

if [ "$NO_LLM" = 1 ]; then
  echo "  --    проверки с LLM пропущены (--no-llm)"
else
  section "ML: LLM"
  ask() { CURL_TIMEOUT=${LLM_WAIT:-120} curl_ml -w '\n%{http_code}' -H 'Content-Type: application/json' -d "$1" "$ML/v1/ask"; }
  # 1. рабочий вопрос: ответ 200 или 400 (нет в базе) — оба значат, что RAG и LLM живы
  r=$(ask "{\"user_id\":\"$UID_T\",\"question\":\"Сколько дней отпуска положено сотруднику?\",\"stage\":null}"); code=$(echo "$r" | tail -1)
  body=$(echo "$r" | head -n -1)
  if [ "$code" = "200" ] && echo "$body" | grep -qE '"code": ?(200|400)'; then ok "POST /v1/ask: $(echo "$body" | cut -c1-90)"
  else fail "POST /v1/ask: HTTP $code $(echo "$body" | cut -c1-120) (ollama/модель/векторная база? docker compose logs ml)"; fi

  # 2. тема тикета
  r=$(CURL_TIMEOUT=${LLM_WAIT:-120} curl_ml -w '\n%{http_code}' -H 'Content-Type: application/json' -d '{"text":"Не работает пропуск, не могу войти в офис"}' "$ML/v1/ticket-title")
  code=$(echo "$r" | tail -1)
  if [ "$code" = "200" ] && echo "$r" | grep -q '"title"' && echo "$r" | grep -q '"category"'; then ok "POST /v1/ticket-title: $(echo "$r" | head -n -1 | cut -c1-100)"
  else fail "POST /v1/ticket-title: HTTP $code $(echo "$r" | head -n -1 | cut -c1-100)"; fi

  # 3. фоновая оценка вопроса: критичная фраза -> запись в stats/events (ждём, ML считает после ответа)
  ask "{\"user_id\":\"$UID_T\",\"question\":\"Я так больше не могу, нет сил, третий месяц до полуночи, буду искать другую работу\",\"stage\":null}" >/dev/null
  stats=""
  for i in $(seq 1 24); do
    stats=$(curl_ml "$ML/v1/stats/$UID_T" 2>/dev/null)
    echo "$stats" | grep -q '"total_requests": *[2-9]' && break
    sleep 5
  done
  if echo "$stats" | grep -q '"total_requests": *[2-9]'; then
    ok "оценка вопросов дописалась в stats (Mongo)"
    ev=$(mongo_js "print(db.getCollection('events').countDocuments({uid:'$UID_T', source:'bot_question'}))" | tail -1)
    [ "${ev:-0}" -ge 2 ] 2>/dev/null && ok "events: записей $ev" || fail "в events нет записей оценки вопросов ($ev)"
    echo "$stats" | grep -q '"red_flag": *true' && ok "критичная фраза -> red_flag" || warn "критичная фраза не подняла red_flag (LLM оценила мягче): $(echo "$stats" | tr -d '\n' | cut -c1-200)"
  else
    fail "статистика не обновилась за 2 минуты: ${stats:-нет ответа}"
  fi

  # 4. диалог тикета через Kafka -> консьюмер -> events (source=ticket)
  closed_at=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  msg=$(printf '{"type":"ticket","uid":"%s","ticket_number":999999,"closed_at":"%s","closed_by":"hr","stage":null,"title":"smoke","category":null,"messages":[{"from":"employee","text":"Не дают пропуск, третий день не могу войти","at":"%s","attachments":0},{"from":"hr","text":"Оформили пропуск, забирайте на ресепшене","at":"%s","attachments":0},{"from":"employee","text":"Спасибо, всё получилось","at":"%s","attachments":0}]}' "$UID_T" "$closed_at" "$closed_at" "$closed_at" "$closed_at")
  if echo "$msg" | dc exec -T kafka /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server localhost:9092 --topic "$TOPIC" >/dev/null 2>&1; then
    found=0
    for i in $(seq 1 24); do
      n=$(mongo_js "print(db.getCollection('events').countDocuments({uid:'$UID_T', source:'ticket', ticket_number:999999}))" | tail -1)
      [ "$n" = "1" ] && { found=1; break; }
      sleep 5
    done
    [ "$found" = 1 ] && ok "диалог тикета: Kafka -> ml-mood-consumer -> events (source=ticket)" \
      || fail "диалог тикета не появился в events за 2 минуты (docker compose logs ml-mood-consumer)"
  else
    fail "не удалось отправить сообщение в Kafka"
  fi
fi

# ---------------------------------------------------------------------------
section "Бот"
if ! dc ps --format '{{.Service}}|{{.State}}' | grep -q '^bot|running'; then
  fail "бот не запущен: docker compose logs --tail=50 bot"
else
  logs=$(dc logs --since 15m bot 2>&1)
  if echo "$logs" | grep -q "bot started" || dc logs --tail 400 bot 2>&1 | grep -q "bot started"; then ok "бот запущен (bot started в логах)"
  else fail "в логах бота нет 'bot started'"; fi
  errs=$(echo "$logs" | grep -ciE "bot error|fatal|answer failed|ticket dialog to kafka failed|MongoServerSelectionError|ECONNREFUSED")
  if [ "$errs" = "0" ]; then ok "в логах бота за 15 минут нет ошибок"
  else
    fail "в логах бота за 15 минут ошибок: $errs"
    echo "$logs" | grep -iE "bot error|fatal|answer failed|ticket dialog|MongoServerSelectionError|ECONNREFUSED" | sort | uniq -c | sort -rn | head -5 | sed 's/^/        /'
  fi
fi

# ---------------------------------------------------------------------------
section "Отчёты (hr-analytics)"
out=$(dc --profile reports run --rm --no-deps hr-analytics python -m app check 2>&1)
echo "$out" | grep -q '✔ PostgreSQL' && ok "hr-analytics -> PostgreSQL" || fail "hr-analytics не видит PostgreSQL: $(echo "$out" | tail -2 | tr '\n' ' ')"
echo "$out" | grep -q '✔ MongoDB' && ok "hr-analytics -> MongoDB" || fail "hr-analytics не видит MongoDB: $(echo "$out" | tail -2 | tr '\n' ' ')"
if echo "$out" | grep -q '✔ SMTP'; then ok "SMTP Яндекс: вход выполнен"
else warn "SMTP: $(echo "$out" | grep 'SMTP' | head -1)"; fi
override=$(env_val REPORT_RECIPIENT_OVERRIDE)
if dc --profile reports ps --format '{{.Service}}|{{.State}}' 2>/dev/null | grep -q '^hr-analytics|running'; then
  [ -n "$override" ] && ok "hr-analytics запущен, письма идут на $override ([TEST])" || warn "hr-analytics запущен БЕЗ REPORT_RECIPIENT_OVERRIDE: письма уходят реальным получателям"
else
  echo "  --    hr-analytics не запущен (профиль reports; запускается вручную)"
fi

# ---------------------------------------------------------------------------
printf '\nИтого: OK %s, WARN %s, FAIL %s\n' "$PASS" "$WARNS" "$FAILS"
[ "$FAILS" = 0 ]
