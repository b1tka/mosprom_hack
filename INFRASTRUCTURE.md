# Инфраструктура бота онбординга (MAX)

Всё поднимается через Docker Compose (проект `onboarding`, сеть `onboarding-net`).

## Запуск

```bash
cp .env.example .env              # поменять пароли, указать BOT_TOKEN
docker compose up -d              # всё, кроме LLM
docker compose --profile llm up -d   # вместе с LLM (Ollama + meno-lite), нужно >= 12 ГБ RAM, см. «Ресурсы»
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
| `db/mongo-init/01_app_user.js` | MongoDB: пользователь приложения (выполняется при создании тома) |
| `bot/` | код бота (см. раздел «Бот») |
| `ml/` | ML-сервис: RAG + настроение (код ML-команды), см. `ml/README.md` |

## Сервисы

| Сервис | Образ | Порты (хост) | Том | Назначение |
|--------|-------|--------------|-----|-----------|
| `postgres` | `postgres:16` | 5432 | `pg_data` | основная БД |
| `mongo` | `mongo:8.0.34` | 27018 (в контейнере 27017) | `mongo_data` | MongoDB; пока только инфраструктура |
| `redis` | `redis:7-alpine` | 6380 (в контейнере 6379) | `redis_data` | состояние диалогов (`appendonly yes`) |
| `s3` | `ghcr.io/snazy/maxio-release:latest` | 9000 (API), 9001 (консоль) | `s3_data` | файлы |
| `s3-init` | тот же | — | — | одноразово создаёт бакет `S3_BUCKET` |
| `kafka` | `apache/kafka:3.9.0` | 29092 | `kafka_data` | брокер для ML-модуля (KRaft, 1 нода) |
| `kafka-init` | `apache/kafka:3.9.0` | — | — | одноразово создаёт топик `onboarding.answers` |
| `ollama` | `ollama/ollama:0.40.10` | — | `ollama_data` | LLM meno-lite-0.1, OpenAI-совместимый API; **профиль `llm`** |
| `ollama-init` | тот же | — | — | одноразово скачивает модель `MENO_MODEL`; профиль `llm` |
| `ml` | сборка из `./ml` (Python 3.12, FastAPI, torch CPU) | 8000 | `ml_data`, `hf_cache` | ML API: RAG (`/v1/ask`) + фоновая оценка настроения |
| `ml-mood-consumer` | тот же образ | — | — | Kafka-консьюмер `onboarding.answers` -> настроение -> Redis |
| `bot` | сборка из `./bot` (Node 22) | — | — | бот MAX, подключён ко всем сервисам выше |

Адреса внутри сети compose: `postgres:5432`, `mongo:27017`, `redis:6379`, `s3:9000`, `kafka:9092`, `ml:8000`, `ollama:11434`.
Адрес Kafka с хоста: `localhost:29092`. Redis с хоста: `localhost:6380` (6379 на хосте может быть занят локальным Redis).
MongoDB с хоста: `localhost:27018` (27017 на хосте занят локальным MongoDB).

### MongoDB
Пока только инфраструктура: назначение, коллекции и индексы будут описаны позже, кодом пока никто не пользуется.
- Root-пользователь (`MONGO_ROOT_USER` / `MONGO_ROOT_PASSWORD`) — только для администрирования.
- Пользователь приложения (`MONGO_APP_USER` / `MONGO_APP_PASSWORD`) с ролью `readWrite` только на базу `MONGO_DB` (`onboarding`);
  создаётся `db/mongo-init/01_app_user.js` при первом старте тома `mongo_data` (повторно не выполняется, как `db/init` у Postgres).
- Сервисам `bot`, `ml`, `ml-mood-consumer` compose передаёт `MONGO_URL=mongodb://<app_user>:<pass>@mongo:27017/onboarding`.
- Консоль: `docker compose exec mongo mongosh -u <root> -p <pass>`.

### S3 / OpenMaxIO
OpenMaxIO — форк веб-консоли MinIO, отдельного образа сервера нет. Образы MinIO на Quay и
Docker Hub не скачались (401 / access denied), поэтому использован неофициальный
`ghcr.io/snazy/maxio-release` (MinIO + mc + консоль OpenMaxIO). Проект-источник, судя по
новостям, не развивается. Образ меняется переменной `S3_IMAGE` в `.env`.
Бакет по умолчанию: `onboarding-files`.

### ML-сервис (`ml/`)
Код ML-команды из веток `ml_ai` -> `mood_find` перенесён в `ml/` (не git merge: ветка `mood_find` удаляла наши
`docker-compose.yml`, `db/` и доки). Ветки `ml_ai`, `mood_find`, `hr_analytics` остаются на сервере как история;
дальнейшая ML-разработка — в `ml/` от `main`. Подробно: `ml/README.md`.

- **RAG**: вопрос -> поиск в Chroma (эмбеддинги `intfloat/multilingual-e5-large`, top-3 чанка) -> LLM meno-lite-0.1 (через Ollama) -> `(код, ответ)`.
- **Настроение**: классификация mood / reason / urgent той же LLM; статистика в Redis `onboarding:stats:<users.id>`.
  Источники: каждый вопрос из `/v1/ask` (в фоне, после ответа) и ответы на вопросы HR из Kafka `onboarding.answers`.
- **База знаний**: исходные документы в S3 `onboarding-files/knowledge/`, векторная база Chroma и `knowledge_base.json` — в томе `ml_data`,
  кэш моделей HuggingFace — в томе `hf_cache`. Полная переиндексация вручную: `docker compose run --rm ml python -m app.ingest build --from-s3`.
  Отдельный документ HR добавляет через бота (`/menu` -> «Добавить файл в базу знаний», см. раздел «Бот»).

Контракт:
- `POST /v1/ask`, тело `{"user_id": "<users.id>", "question": "<строка>", "stage": null}` -> HTTP 200, тело `{"code": 200 | 400, "answer": "<строка>"}`.
  `code: 200` — ответ из базы или вежливый отказ на нерабочий вопрос; `code: 400` — рабочий вопрос, но в базе ответа нет.
- `503` — LLM или векторная база недоступны (в т.ч. база пуста или профиль `llm` не запущен); `422` — невалидный запрос.
- `POST /v1/documents`, тело `{"key": "incoming/<id>/<имя.pdf|.txt|.md>"}` — ML скачивает файл из S3, режет на чанки (OCR для сканов),
  добавляет в Chroma и `knowledge_base.json` (одноимённый документ заменяется) и переносит файл в `knowledge/<имя>`.
  Ответ 200 `{"source", "chunks_added", "replaced"}`; 400 — неверный ключ/формат или текст не извлёкся (входящий файл удаляется);
  404 — нет файла в S3; 503 — сбой S3 или Chroma. Синхронный, может идти минуты; документы индексируются по одному (блокировка в процессе).
- `GET /v1/stats/{user_id}`, `GET /health`; OpenAPI: http://localhost:8000/docs
- Бот не зависит от `ml` при старте; таймаут запроса 90 с (`ML_TIMEOUT_MS`): LLM до 60 с (`LLM_TIMEOUT`) + поиск.

### Ресурсы
| Компонент | RAM |
|-----------|-----|
| LLM meno-lite-0.1 `Q4_K_M` (Ollama, CPU) | ~5–6 ГБ (4.65 ГБ веса + контекст) |
| эмбеддинги e5-large в `ml` | ~0.9 ГБ в простое (замер), до ~2.5 ГБ под нагрузкой |
| `ml-mood-consumer` | ~0.5 ГБ |
| Kafka, Postgres, Redis, S3, бот | ~1.5–2 ГБ |

С LLM нужно **не меньше 12 ГБ RAM** для Docker (рекомендуется 16 ГБ). Легче: `MENO_MODEL=hf.co/bond005/meno-lite-0.1-gguf:IQ3_M` (3.54 ГБ).
Без профиля `llm` стек занимает ~4–5 ГБ. На dev-ноутбуке (8 ГБ для Docker) LLM не запускается.
Диск: образ `ml` 3.25 ГБ (torch CPU, easyocr), модель эмбеддингов 2.2 ГБ (том `hf_cache`), LLM 4.65 ГБ (том `ollama_data`).

Первый старт `ml` скачивает e5-large (~2.2 ГБ); на медленной сети (~1 МБ/с) это ~40 минут, healthcheck на это время показывает
`unhealthy` — это не ошибка. Протокол Xet у HuggingFace на такой сети зависал, поэтому в compose `HF_HUB_DISABLE_XET=1`.
Индексация 4 PDF (`ingest build --from-s3`) на CPU заняла ~11 минут: скан на 23 страницы распознаётся OCR. Во время индексации `ml` лучше
остановить (`docker compose stop ml`), затем `docker compose up -d ml`.

### Kafka
Топик `onboarding.answers` (3 партиции, RF 1, имя — `KAFKA_TOPIC_ANSWERS`): ответы сотрудников на вопросы HR
`{uid: users.id, hr_question, answer, stage}`, ключ = `uid`. Пишет бот (`sendHrAnswer` в `bot/src/kafka.js`; опросы HR в боте
пока не реализованы), читает `ml-mood-consumer`. Старые топики `ml.requests` / `ml.responses` больше не используются
(в уже созданном брокере остались, на работу не влияют).

### Известная проблема: формат статистики
`mood` (ML) и ветка `hr_analytics` (отчёты HR, в `main` не влита) используют один ключ Redis `onboarding:stats:<users.id>`,
но разный JSON: ML пишет `total_requests`, `moods`, `reasons`, `last_reason`; hr_analytics ждёт `request_count`, `mood`,
`request_tags`, `reason`. Перед подключением отчётов форматы нужно согласовать.

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
| `src/index.js` | `/start`, `/id`, вопросы сотрудников -> ML API, запуск и остановка |
| `src/config.js` | чтение env (дефолты для запуска с хоста) |
| `src/db.js` | пул Postgres, `findUserByMaxId`, `listTags`, `listDirectors`, функции приглашений |
| `src/invite.js` | `/menu`, `/cancel`; добавление сотрудника для HR (опрос, приглашение, QR); активация сотрудника по QR |
| `src/phone.js` | нормализация телефона, проверка почты |
| `src/state.js` | состояние диалога в Redis, ключ `dialog:<max_id>` |
| `src/kafka.js` | продюсер; `sendHrAnswer` -> `onboarding.answers` (пока не вызывается) |
| `src/knowledge.js` | загрузка HR-ом документа в базу знаний: файл из чата -> S3 -> `POST /v1/documents` -> уведомление |
| `src/ml.js` | HTTP-клиент ML: `ask({ userId, question, stage })` -> `/v1/ask`, `addDocument({ key })` -> `/v1/documents` |
| `src/s3.js` | `putFile` / `getFile` / `deleteFile` в бакете |

Команды: `/start`, `/id` (свой `max_id`), `/menu` (интерактивное меню; пункты «Добавить нового сотрудника» и «Добавить файл в базу знаний» только для `hr` и `hr_head`), `/cancel`.

**Документ в базу знаний.** `/menu` -> «Добавить файл в базу знаний» (шаг `kb_await_file`) -> HR присылает файл (PDF, TXT, MD, до 50 МБ).
Бот сразу отвечает «принят», а дальше в фоне: скачивает файл из MAX, кладёт в S3 `incoming/<uuid>/<имя>`, вызывает `POST /v1/documents`
и присылает HR уведомление: «добавлен/обновлён, фрагментов: N» или причину ошибки. Фоном — потому что polling библиотеки ждёт обработчики,
и индексация (минуты) иначе заблокировала бы бота для всех. При ошибке входящий файл удаляется из S3. Имя файла — имя документа в базе,
одноимённый заменяется. Таймаут индексации 30 мин (`ML_INDEX_TIMEOUT_MS`); запрос идёт через `node:http`, т.к. у `fetch` свой лимит 5 мин.

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
Обычное текстовое сообщение зарегистрированного пользователя:
1. роль `employee` — это вопрос: бот вызывает `POST /v1/ask` с `user_id = users.id` и отправляет пользователю `answer`
   (и при `code 200`, и при `code 400` — текст формирует ML); техническая ошибка или таймаут -> «Сервис ответов временно недоступен»,
   подробности в `docker compose logs bot` (`ml api failed`). Настроение по вопросу ML считает сам, в фоне;
2. другие роли получают подсказку «Откройте /menu».

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
`KAFKA_EXTERNAL_PORT`, `KAFKA_TOPIC_ANSWERS`, `ML_PORT`, `MENO_MODEL` (модель для Ollama),
`MONGO_PORT`, `MONGO_DB`, `MONGO_ROOT_USER`, `MONGO_ROOT_PASSWORD`, `MONGO_APP_USER`, `MONGO_APP_PASSWORD`.
В контейнер бота compose передаёт `ML_API_URL=http://ml:8000` и `ML_TIMEOUT_MS=90000` (`ML_INDEX_TIMEOUT_MS` — по умолчанию 30 мин). Остальные настройки ML (`RAG_TOP_K`, `EMBED_MODEL`,
`LLM_TIMEOUT`, `CHUNK_SIZE` и т.д.) — переменные окружения из `ml/app/config.py`, при необходимости добавляются в `environment` сервиса `ml`.

Значения паролей в `.env.example` — заглушки, перед реальным использованием заменить.

## Проверено

- Postgres: схема (включая `email`, `phone`, `invites`), 4 роли и 3 тега; миграции применены на живой БД, `db/init` проверен на чистой БД.
- Redis: `PONG`; бот пишет и читает состояние диалога.
- MongoDB: healthy; пользователь приложения создан, пишет и читает в базе `onboarding`, к `admin` доступа нет (`Unauthorized`);
  `MONGO_URL` есть в контейнерах `bot`, `ml`, `ml-mood-consumer`; порт 27018 доступен с хоста.
- S3: бакет `onboarding-files` создаётся, `putFile` / `getFile` работают; 4 PDF базы знаний загружены в `knowledge/` через `ingest upload`.
- MAX: токен валиден (`/me`: бот «Корсик», `@se14753471_bot`), контейнер `bot` работает без перезапусков, `/start`, `/id` и опрос регистрации отвечали в живом MAX.
- Поток `/menu` -> добавление сотрудника -> активация прогнан на моках контекста с реальными Postgres и Redis: права, валидация почты и телефона, QR, чужой контакт, неверный номер, повторное использование токена, двойное подтверждение, повторы и запасной вариант при сбое загрузки картинки.
- ML (без LLM):
  - образ `onboarding-ml` собирается (3.25 ГБ); 14/14 офлайн-тестов ML-команды (`tests/test_offline.py`) проходят в контейнере;
  - `ml` healthy, `/health` -> ok; при пустой базе `/v1/ask` -> 503 с подсказкой про ingest; без `user_id` -> 422; `/v1/stats/<id>` без данных -> 404;
  - `ingest build --from-s3`: 146 чанков (столько же, сколько в `knowledge_base.json` ML-команды), Chroma в томе `ml_data`; поиск находит
    нужные документы («отпуск» -> Правила внутреннего трудового распорядка, «коммерческая тайна» -> Положение о коммерческой тайне);
  - после индексации `/v1/ask` доходит до LLM и без неё за ~8 с отдаёт 503;
  - `ml-mood-consumer` читает `onboarding.answers`, без LLM логирует ошибку классификации, коммитит сообщение и не падает.
- Бот: `ask()` шлёт `{user_id, question, stage}` на `/v1/ask`; на `code 200`/`400` возвращает `answer`, на 503/422/сеть — исключение
  (пользователь получает «Сервис ответов временно недоступен»); против живого `ml` (с хоста и из контейнера бота) — 503 -> исключение;
  бот стартует и работает без `ml`, ошибок Kafka в логах нет.
- Загрузка в базу знаний (из контейнера бота, `putFile` + `addDocument` против живых S3 и `ml`): новый TXT -> добавлен (1 чанк, `replaced: false`),
  повтор -> `replaced: true`; файл без текста -> 400 и удалён из S3; нет файла -> 404; ключ не из `incoming/` или с `..` -> 400;
  после успеха файл перенесён в `knowledge/`, из `incoming/` удалён; запущенный `ml` сразу находит новый документ поиском.
  Тестовый документ удалён (`ingest delete --s3`), в базе снова 146 чанков.

## Не проверено

С LLM (проверять на сервере с `docker compose --profile llm up -d`; на dev-ноутбуке не хватает памяти):
- скачивание модели `ollama-init` и ответы `/v1/ask`: 200 (ответ из базы), 400 (нет в базе), 200 + отказ (нерабочий вопрос);
- фоновая классификация вопроса и запись статистики `onboarding:stats:<users.id>`; то же через Kafka-консьюмер;
- время ответа на CPU (таймаут бота 90 с).

В живом MAX:
- вопрос от сотрудника с ролью `employee` и ответ бота (проверяет второй человек с этой ролью);
- формат ссылки `https://max.ru/<бот>?start=<токен>` и доставка payload (`bot_started` или `/start <токен>`);
- кнопка «Поделиться контактом»: приходит ли `max_info.user_id` и телефон в `vcf_info`;
- показ QR-картинки HR;
- приём файла от HR: поля вложения `file` (`payload.url`, `filename`, `size`) и скачивание по `payload.url`; уведомление после индексации;
- единичный сбой загрузки картинки (`upload.request.error`, «Network error») воспроизвести не удалось.

Также: сохранность ключа Redis после рестарта контейнера `redis`; отправка `sendHrAnswer` из бота (опросов HR пока нет).

Примечание для локальной проверки: если в shell задан `http_proxy`, запросы `curl localhost:8000` уходят в прокси и получают от него 503 —
используйте `curl --noproxy '*' ...`.
