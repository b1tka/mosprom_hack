# CLAUDE.md

Бот для мессенджера MAX: онбординг нового сотрудника в компанию. Отвечать на русском.

## Статус
Инфраструктура в Docker, работающий бот (`bot/`, JS, `@maxhub/max-bot-api`) и ML-сервис (`ml/`, FastAPI): RAG по базе знаний и оценка настроения — код ML-команды, перенесён из веток `ml_ai`/`mood_find`. ML-разработка дальше — в `ml/` от `main`.
Подробности: `INFRASTRUCTURE.md`. Документация MAX для JS: https://dev.max.ru/docs/chatbots/bots-coding/js

## Стек
- Postgres 16 — `users`, `role`, `tag`, `invites` (`db/init/*.sql` выполняются только при первом старте тома; для живой БД — `db/migrations/`, вручную).
- MongoDB 8 (на хосте порт 27018; 27017 занят локальным MongoDB) — пока только инфраструктура, назначение опишет пользователь. Пользователь приложения с `readWrite` на базу `onboarding` (`db/mongo-init/`), сервисам передаётся `MONGO_URL`.
- Redis 7 (на хосте порт 6380) — состояние диалогов бота (`dialog:<max_id>`), переживает рестарт.
- S3 — OpenMaxIO (форк консоли MinIO), образ `ghcr.io/snazy/maxio-release` (неофициальный; MinIO на Quay/Docker Hub не скачался). Бакет `onboarding-files`.
- Kafka (KRaft, 1 нода) — топик `onboarding.answers`: ответы сотрудников на вопросы HR `{uid, hr_question, answer, stage}` -> `ml-mood-consumer` -> настроение. Бот: `sendHrAnswer` (опросы HR ещё не сделаны).
- ML — `ml/` (Python 3.12, FastAPI, порт 8000; один образ: `ml` API и `ml-mood-consumer`). `POST /v1/ask {user_id: users.id, question, stage}` -> HTTP 200 `{code: 200|400, answer}`, 503 — LLM/база недоступны. RAG: Chroma (том `ml_data`) + эмбеддинги e5-large + LLM meno-lite-0.1 в `ollama` (compose-профиль `llm`). Документы базы знаний — в S3 `knowledge/`, индексация `python -m app.ingest`. Статистика настроения — Redis `onboarding:stats:<users.id>`. `POST /v1/documents {key: incoming/<id>/<имя>}` — добавить документ из S3 в базу (переносит в `knowledge/`). Контракт — `ml/README.md`.
- Бот — `bot/src`: `index.js` (точка входа, `/start`, `/id`; сообщения сотрудника `employee` -> `POST /v1/ask` через `ml.js`, ответ — текст от ML), `invite.js` (`/menu`, `/cancel`, добавление сотрудника, активация по QR), `knowledge.js` (HR в `/menu` присылает файл -> S3 `incoming/` -> `/v1/documents` в фоне -> уведомление), `db.js`, `state.js`, `kafka.js`, `s3.js`, `phone.js`, `config.js`.

## Команды
```bash
cp .env.example .env               # один раз, поменять пароли, указать BOT_TOKEN
docker compose up -d
docker compose ps -a
docker compose up -d --build bot   # после любых правок кода бота
docker compose --profile llm up -d # вместе с LLM (нужно >= 12 ГБ RAM; на dev-ноутбуке НЕ запускать)
docker compose up -d --build ml ml-mood-consumer   # после правок ML
docker compose run --rm ml python -m app.ingest build --from-s3   # пересобрать базу знаний из S3
docker compose run --rm --no-deps ml python -m unittest discover -s tests -v
docker compose logs bot
docker compose down -v             # сброс данных, init-скрипты БД выполнятся заново
```
Адреса внутри сети compose: `postgres:5432`, `mongo:27017`, `redis:6379`, `s3:9000`, `kafka:9092`, `ml:8000`, `ollama:11434`. С хоста: Kafka `localhost:29092`, Redis `localhost:6380`, MongoDB `localhost:27018`, ML `localhost:8000`.

## Доменная модель
- Роли: новый сотрудник (`employee`), руководитель подразделения (`director`), HR-менеджер (`hr`), руководитель по подбору и развитию персонала (`hr_head`). Слаги — предположение, в UML были только `employee | director`.
- Теги (GTP, GrPP, Luzhniki) только у нового сотрудника, у остальных ролей `tag_id` пуст. БД это не проверяет — контролировать в коде.
- У руководителя может быть много новых сотрудников, у сотрудника один руководитель (`users.director_id`, NULL у руководителей).
- `users.max_id` — id пользователя в MAX, уникален. `users.email`, `users.phone` необязательные.

## Как появляются сотрудники
Самостоятельной регистрации нет. `hr` и `hr_head` в `/menu` выбирают «Добавить нового сотрудника», заполняют данные и получают QR (ссылка `max.ru/<бот>?start=<токен>`, 7 дней, одноразовая, в БД только хэш). Сотрудник сканирует QR и жмёт «Поделиться контактом»: контакт должен быть его собственным, а номер — совпасть с введённым HR. Только после этого создаётся `users` (роль `employee`). Директоров, HR и `hr_head` добавляют вручную в БД.

## Заметки
- `.env` в `.gitignore` (там токен бота и пароли); пароли в `.env.example` — заглушки. Токен не коммитить и не выводить в логи.
- MAX API (`platform-api2.max.ru`) подписан УЦ Минцифры: корневой сертификат в `bot/certs/`, подключён через `NODE_EXTRA_CA_CERTS` в Dockerfile.
- На хосте уже есть локальный Redis с паролем на 127.0.0.1:6379 — поэтому наш проброшен на 6380.
- Библиотека MAX скрывает причину сетевых ошибок загрузки («Network error»), при сбоях смотреть `docker compose logs bot`.
- Коммит и пуш — только по явной команде пользователя, без строки `Claude-Session`. Роль Евгения (`hr`) для тестов не менять: функции `employee` проверяет второй человек.
- LLM на этом ноутбуке не запускать (8 ГБ на Docker, нужно >= 12): работать без профиля `llm`; после проверок модель не оставлять запущенной. Запуск планируется на другой машине.
- `hr_analytics` (ветка) читает тот же ключ статистики, но в другом формате JSON — согласовать перед влитием.
- База знаний проиндексирована (146 чанков в томе `ml_data`, PDF в S3 `knowledge/`). Без LLM `/v1/ask` отдаёт 503 — это ожидаемо на dev-ноутбуке.
- В shell задан `http_proxy`: для `curl localhost:...` нужен `--noproxy '*'`, иначе ответ 503 от прокси.
- В живом MAX не проверено: ответы на вопросы сотрудника, формат deep-link со `start`, кнопка «Поделиться контактом», показ QR, приём файла от HR.
- Долгие операции в обработчиках бота (индексация документа) — только в фоне: polling библиотеки ждёт все обработчики пачки, иначе бот замирает для всех. Список — в `INFRASTRUCTURE.md`.
