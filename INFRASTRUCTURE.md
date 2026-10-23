# Инфраструктура бота онбординга (MAX)

Всё поднимается через Docker Compose (проект `onboarding`, сеть `onboarding-net`).

## Запуск

```bash
cp .env.example .env   # поменять пароли
docker compose up -d
docker compose ps -a
```

Сброс данных (в т.ч. чтобы заново выполнились init-скрипты БД): `docker compose down -v`.

## Файлы

| Файл | Назначение |
|------|-----------|
| `docker-compose.yml` | все сервисы |
| `.env.example` | шаблон переменных (`.env` в `.gitignore`) |
| `db/init/01_schema.sql` | схема Postgres |
| `db/init/02_seed.sql` | роли и теги |

## Сервисы

| Сервис | Образ | Порты (хост) | Том | Назначение |
|--------|-------|--------------|-----|-----------|
| `postgres` | `postgres:16` | 5432 | `pg_data` | основная БД |
| `redis` | `redis:7-alpine` | 6379 | `redis_data` | состояние диалогов (`appendonly yes`) |
| `s3` | `ghcr.io/snazy/maxio-release:latest` | 9000 (API), 9001 (консоль) | `s3_data` | файлы |
| `s3-init` | тот же | — | — | одноразово создаёт бакет `S3_BUCKET` |
| `kafka` | `apache/kafka:3.9.0` | 29092 | `kafka_data` | брокер для ML-модуля (KRaft, 1 нода) |
| `kafka-init` | `apache/kafka:3.9.0` | — | — | одноразово создаёт топики |

Адреса внутри сети compose: `postgres:5432`, `redis:6379`, `s3:9000`, `kafka:9092`.
Адрес Kafka с хоста: `localhost:29092`.

### S3 / OpenMaxIO
OpenMaxIO — форк веб-консоли MinIO, отдельного образа сервера нет. Образы MinIO на Quay и
Docker Hub не скачались (401 / access denied), поэтому использован неофициальный
`ghcr.io/snazy/maxio-release` (MinIO + mc + консоль OpenMaxIO). Проект-источник, судя по
новостям, не развивается. Образ меняется переменной `S3_IMAGE` в `.env`.
Бакет по умолчанию: `onboarding-files`.

### Kafka
Топики (3 партиции, replication factor 1): `ml.requests` (бот → ML), `ml.responses` (ML → бот).
Имена задаются `KAFKA_TOPIC_REQUESTS` / `KAFKA_TOPIC_RESPONSES`.

## База данных

Таблицы по UML: `role`, `tag`, `users`.

- `users.role_id → role.id` — `RESTRICT`
- `users.tag_id → tag.id` — `SET NULL`
- `users.director_id → users.id` — `RESTRICT` (NULL у руководителей)
- `users.max_id` — `bigint`, `UNIQUE NOT NULL`
- индексы: `director_id`, `role_id`
- триггер `users_set_updated_at` обновляет `updated_at` при `UPDATE`

Сиды:
- `role`: `employee` (новый сотрудник), `director` (руководитель подразделения),
  `hr` (HR-менеджер), `hr_head` (руководитель по подбору и развитию персонала).
  Слаги — предположение, в UML были только `employee | director`.
- `tag`: `GTP`, `GrPP`, `Luzhniki`.

Не реализовано: ограничение «тег только у `employee`» (контролировать в коде бота).

## Переменные окружения (`.env`)

`POSTGRES_USER/PASSWORD/DB/PORT`, `REDIS_PORT`, `S3_IMAGE`, `S3_ROOT_USER`,
`S3_ROOT_PASSWORD`, `S3_BUCKET`, `S3_API_PORT`, `S3_CONSOLE_PORT`,
`KAFKA_EXTERNAL_PORT`, `KAFKA_TOPIC_REQUESTS`, `KAFKA_TOPIC_RESPONSES`.

Значения паролей в `.env.example` — заглушки, перед реальным использованием заменить.

## Проверено

- Postgres: схема, 4 роли и 3 тега на месте.
- Redis: `PONG`.
- S3: бакет `onboarding-files` создан.
- Kafka: топики `ml.requests`, `ml.responses` созданы.

Не проверялось: сохранность ключа Redis после рестарта, produce/consume в Kafka.
