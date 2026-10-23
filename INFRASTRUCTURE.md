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
| `db/migrations/` | миграции для уже созданной БД (применяются вручную) |
| `bot/` | код бота (см. раздел «Бот») |

## Сервисы

| Сервис | Образ | Порты (хост) | Том | Назначение |
|--------|-------|--------------|-----|-----------|
| `postgres` | `postgres:16` | 5432 | `pg_data` | основная БД |
| `redis` | `redis:7-alpine` | 6380 (в контейнере 6379) | `redis_data` | состояние диалогов (`appendonly yes`) |
| `s3` | `ghcr.io/snazy/maxio-release:latest` | 9000 (API), 9001 (консоль) | `s3_data` | файлы |
| `s3-init` | тот же | — | — | одноразово создаёт бакет `S3_BUCKET` |
| `kafka` | `apache/kafka:3.9.0` | 29092 | `kafka_data` | брокер для ML-модуля (KRaft, 1 нода) |
| `kafka-init` | `apache/kafka:3.9.0` | — | — | одноразово создаёт топики |
| `bot` | сборка из `./bot` (Node 22) | — | — | бот MAX, подключён ко всем сервисам выше |

Адреса внутри сети compose: `postgres:5432`, `redis:6379`, `s3:9000`, `kafka:9092`.
Адрес Kafka с хоста: `localhost:29092`. Redis с хоста: `localhost:6380` (6379 на хосте может быть занят локальным Redis).

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

Таблицы: `role`, `tag`, `users` (по UML) и `invites` (приглашения сотрудников, добавлена позже).

- `users.role_id → role.id` — `RESTRICT`
- `users.tag_id → tag.id` — `SET NULL`
- `users.director_id → users.id` — `RESTRICT` (NULL у руководителей)
- `users.max_id` — `bigint`, `UNIQUE NOT NULL`
- `users.email`, `users.phone` — `varchar`, nullable, без проверки формата и уникальности (добавлены миграцией `db/migrations/001_users_email_phone.sql`)
- индексы: `director_id`, `role_id`
- триггер `users_set_updated_at` обновляет `updated_at` при `UPDATE`

Сиды:
- `role`: `employee` (новый сотрудник), `director` (руководитель подразделения),
  `hr` (HR-менеджер), `hr_head` (руководитель по подбору и развитию персонала).
  Слаги — предположение, в UML были только `employee | director`.
- `tag`: `GTP`, `GrPP`, `Luzhniki`.

### Таблица `invites`
Приглашение, по которому сотрудник станет записью в `users` (до активации его `max_id` неизвестен, поэтому отдельная таблица).

| Поле | Описание |
|------|----------|
| `token_hash` | `sha256(токен)`, `UNIQUE`; сам токен только в QR/ссылке |
| `name`, `surname`, `tag_id`, `director_id`, `email`, `phone` | данные, введённые HR (`phone` только цифры, `7XXXXXXXXXX`) |
| `created_by` | `users.id` HR, создавшего приглашение (`ON DELETE CASCADE`) |
| `expires_at` | считается в БД: `now() + 7 дней` |
| `used_at`, `used_by_max_id` | заполняются при активации |

### Миграции
Для уже созданной БД применяются вручную:
`docker compose exec -T postgres psql -U onboarding < db/migrations/<файл>.sql`.
Те же изменения внесены в `db/init/01_schema.sql` для чистых установок (init-скрипты работают только при первом старте тома).
- `001_users_email_phone.sql` — `users.email`, `users.phone`
- `002_invites.sql` — таблица `invites`

Не реализовано: ограничение «тег только у `employee`» (контролировать в коде бота).

### Текущее содержимое БД (dev)
`role` — 4 строки, `tag` — 3, `invites` — пусто, `users` — одна запись: Евгений Мерескин, роль `hr`, `max_id` 385032755.
Директоров, HR и руководителей по подбору в `users` добавляют вручную в БД: через бота создаются только `employee`.

## Бот (`bot/`)

JS (ESM, Node >= 20), библиотека `@maxhub/max-bot-api`. Токен: `BOT_TOKEN` в `.env`.

| Файл | Что делает |
|------|-----------|
| `src/index.js` | `/start`, `/id`, сообщения -> ML, запуск и остановка |
| `src/config.js` | чтение env (дефолты для запуска с хоста) |
| `src/db.js` | пул Postgres, `findUserByMaxId`, `listTags`, `listDirectors`, функции приглашений |
| `src/invite.js` | `/menu`, `/cancel`; добавление сотрудника для HR (опрос, приглашение, QR); активация сотрудника по QR |
| `src/phone.js` | нормализация телефона, проверка почты |
| `src/state.js` | состояние диалога в Redis, ключ `dialog:<max_id>` |
| `src/kafka.js` | `sendToMl` -> `ml.requests`, consumer `ml.responses` |
| `src/s3.js` | `putFile` / `getFile` в бакете |

Команды: `/start`, `/id` (свой `max_id`), `/menu` (интерактивное меню; пункт «Добавить нового сотрудника» только для `hr` и `hr_head`), `/cancel`.

**Добавление сотрудника.** `/menu` -> «Добавить нового сотрудника» -> имя -> фамилия -> тег (кнопки) -> руководитель (кнопки) -> почта -> телефон ->
подтверждение. Бот создаёт запись в `invites` и присылает HR QR-код (PNG, загружается в MAX) со ссылкой
`https://max.ru/<username_бота>?start=<токен>`. Токен: 24 случайных байта, в БД только sha256-хэш,
срок 7 дней, одноразовый. Новое приглашение на тот же номер гасит предыдущее. Нельзя создать приглашение на номер, который уже есть в `users`.
Если загрузка картинки в MAX не удалась, она повторяется до 3 раз; если не помогло, HR получает только ссылку (приглашение не теряется). Сбои логируются: `qr upload failed (attempt N/3)`.

**Активация.** Сотрудник сканирует QR -> бот (событие `bot_started` с payload или `/start <токен>`) просит нажать
«Поделиться контактом». Проверки: контакт принадлежит самому отправителю (`max_info.user_id`), номер из контакта
равен номеру, введённому HR. До 5 неудачных попыток. При успехе создаётся `users` (роль `employee`, поля из приглашения),
приглашение гасится, HR получает уведомление. Всё в одной транзакции.

Шаги диалога (`add_*`, `await_contact`) хранятся в Redis и переживают рестарт бота.
Самостоятельной регистрации нет. Неизвестному пользователю бот предлагает обратиться к HR.
Права на «Добавить нового сотрудника» проверяются при показе меню, при нажатии кнопки и на каждом шаге опроса.

Поток: `/start` ищет пользователя по `max_id` (нет в БД — предлагает обратиться к HR), ставит шаг `idle`.
Обычное сообщение уходит в `ml.requests` как `{maxId, text, role, tag, ts}` (ключ = `maxId`), шаг `waiting_ml`.
ML-модуль отвечает в `ml.responses` сообщением `{maxId, text}` — бот отправляет текст пользователю, шаг `idle`.

TLS: библиотека ходит на `platform-api2.max.ru`, сертификат подписан российским УЦ Минцифры
(Russian Trusted Root CA), которого нет в стандартных хранилищах. Корневой сертификат лежит в
`bot/certs/russian_trusted_root_ca.pem` (источник: gu-st.ru, SHA-256 `D2:6D:2D:02:...:CF:31`, до 2032)
и подключён в Dockerfile через `NODE_EXTRA_CA_CERTS` (только для процесса бота).
При запуске с хоста: `NODE_EXTRA_CA_CERTS=bot/certs/russian_trusted_root_ca.pem npm run dev`.

Запуск: `docker compose up -d --build bot` (после правок кода бота нужна пересборка). Логи: `docker compose logs bot`. Локально: `cd bot && npm install && npm run dev`
(с хоста нужны `BOT_TOKEN` и порты из `.env`; дефолты в `config.js` под `localhost`).

## Переменные окружения (`.env`)

`BOT_TOKEN` (токен бота из MAX, обязателен), `POSTGRES_USER/PASSWORD/DB/PORT`, `REDIS_PORT`, `S3_IMAGE`, `S3_ROOT_USER`,
`S3_ROOT_PASSWORD`, `S3_BUCKET`, `S3_API_PORT`, `S3_CONSOLE_PORT`,
`KAFKA_EXTERNAL_PORT`, `KAFKA_TOPIC_REQUESTS`, `KAFKA_TOPIC_RESPONSES`.

Значения паролей в `.env.example` — заглушки, перед реальным использованием заменить.

## Проверено

- Postgres: схема (включая `email`, `phone`, `invites`), 4 роли и 3 тега; миграции применены на живой БД, `db/init` проверен на чистой БД.
- Redis: `PONG`; бот пишет и читает состояние диалога.
- S3: бакет `onboarding-files` создаётся, `putFile` / `getFile` работают.
- Kafka: топики `ml.requests`, `ml.responses` созданы; сообщение бота дошло в `ml.requests`, ответ из `ml.responses` принят consumer-ом.
- MAX: токен валиден (`/me`: бот «Корсик», `@se14753471_bot`), контейнер `bot` работает без перезапусков, `/start`, `/id` и опрос регистрации отвечали в живом MAX (в БД были реальные пользователи, созданные через бота).
- Поток `/menu` -> добавление сотрудника -> активация прогнан на моках контекста с реальными Postgres и Redis: права, валидация почты и телефона, QR (PNG генерируется), чужой контакт, неверный номер, повторное использование токена, двойное подтверждение, повторы и запасной вариант при сбое загрузки картинки.

## Не проверено в живом MAX

- формат ссылки `https://max.ru/<бот>?start=<токен>` и доставка payload (`bot_started` или `/start <токен>`);
- кнопка «Поделиться контактом»: приходит ли `max_info.user_id` и телефон в `vcf_info`;
- показ QR-картинки HR;
- единичный сбой загрузки картинки (`upload.request.error`, «Network error») воспроизвести не удалось: 18 попыток подряд прошли.

Не проверялось также: сохранность ключа Redis после рестарта контейнера `redis`, ответы ML-модуля (самого модуля пока нет).
