# ML-сервис «Корсик»

RAG-ответы на вопросы новых сотрудников и оценка их настроения. Код написан ML-командой
(ветки `ml_ai` → `mood_find`) и перенесён сюда, в `ml/` на `main`. Дальнейшая ML-разработка ведётся здесь.

## Устройство

```
бот ──HTTP POST /v1/ask──► ml (FastAPI, app/api.py)
                             ├─ rag.py:   вопрос → поиск в Chroma (эмбеддинги e5-large) → LLM → (код, ответ)
                             └─ фоном mood.py: классификация вопроса → статистика в Mongo (stats + events)
бот ──Kafka onboarding.answers──► ml-mood-consumer (app/kafka_consumer.py) → mood.py → статистика в Mongo
  (ответы на вопросы HR и полные диалоги закрытых тикетов)
hr-analytics (hr_analytics/) читает Mongo stats/events → отчёты по ролям на почту
LLM meno-lite-0.1 ── сервис ollama (OpenAI-совместимый API), профиль compose `llm`
Документы базы знаний ── S3 `onboarding-files/knowledge/` → ingest.py → Chroma (том ml_data)
```

Один Docker-образ на три роли: API (по умолчанию), консьюмер (`python -m app.kafka_consumer`),
индексация (`python -m app.ingest ...`).

| Модуль | Что делает |
|--------|-----------|
| `app/api.py` | FastAPI: `/v1/ask`, `/v1/stats/{user_id}`, `/health`; при старте грузит эмбеддинги |
| `app/rag.py` | `ask_rag(question) -> (код, ответ)` |
| `app/mood.py` | классификация mood/reason/urgent и обновление статистики |
| `app/kafka_consumer.py` | консьюмер `onboarding.answers` |
| `app/ingest.py` | документы → текст (OCR для сканов) → чанки → JSON + Chroma; загрузка в S3 |
| `app/s3_docs.py` | работа с документами в S3 |
| `app/vectorstore.py`, `app/llm.py`, `app/store.py`, `app/prompts.py`, `app/config.py` | векторная база, клиент LLM, хранилище статистики, промпты, настройки (всё через env) |
| `tests/test_offline.py` | тесты логики без LLM, Chroma, Mongo, Kafka |
| `demo.py` | ручная отладка из консоли |

## Контракт

### `POST /v1/ask`
Запрос: `{"user_id": "<users.id, UUID>", "question": "<строка>", "stage": null}`

Ответ HTTP 200: `{"code": 200 | 400, "answer": "<строка>"}`
- `code: 200` — ответ найден в базе, либо вопрос нерабочий (тогда фиксированный вежливый отказ);
- `code: 400` — рабочий вопрос, но в базе ответа нет (текст «…передаю ваш вопрос HR-специалисту»).

Другие статусы: `503` — LLM или векторная база недоступны (в т.ч. база не проиндексирована), `422` — невалидный запрос.
Бот пересылает пользователю `answer` при 200 и 400, при прочих — «сервис временно недоступен».

После ответа в фоне классифицируется вопрос и обновляется статистика пользователя в Mongo (`stats` + запись в `events`).

### `POST /v1/documents` — добавить документ из S3 (вызывает бот, когда HR присылает файл)
Запрос: `{"key": "incoming/<id>/<имя.pdf|.txt|.md>"}` — бот кладёт файл в S3 под префикс `S3_INCOMING_PREFIX` (`incoming/`).

Ответ 200: `{"source": "<имя>", "chunks_added": N, "replaced": true|false}`. Файл переносится в `knowledge/<имя>`;
одноимённый документ заменяется и в S3, и в векторной базе.
- `400` — ключ не вида `incoming/<id>/<имя>` или неподдерживаемый формат; либо текст не извлёкся (файл удаляется из `incoming/`);
- `404` — файла нет в S3; `503` — сбой S3 или Chroma.

Синхронный: может идти минуты (OCR). Индексация в процессе API идёт по одному документу (`threading.Lock`);
одновременно с `ingest build` в другом контейнере не запускать.

### `GET /v1/documents` → `{"documents": ["<имя>", ...]}`
Объединение проиндексированных (`knowledge_base.json`) и лежащих в S3 `knowledge/`, по алфавиту. `503` — S3 недоступен.

### `DELETE /v1/documents/{имя}` → `{"source", "chunks_deleted"}`
Удаляет документ из `knowledge_base.json`, Chroma и S3 `knowledge/`. `404` — нигде нет, `400` — имя с путём, `503` — сбой.
Берёт ту же блокировку, что и индексация, поэтому во время добавления документа ждёт его окончания.

### `POST /v1/ticket-title` — тема обращения к HR
Запрос: `{"text": "<вопрос сотрудника>"}` → `{"title": "<3-6 слов>", "category": "<одна из reasons классификатора>"}` (`app/ticket.py`,
промпт `TICKET_TITLE_PROMPT`). `503` — LLM недоступна или ответ не разобрался; бот тогда оставляет временный заголовок (начало вопроса).

### `GET /v1/stats/{user_id}` → `{"<user_id>": {...}}` или 404
### `GET /health` → `{"status": "ok"}`

### Kafka `onboarding.answers`
Ключ сообщения — `uid`. Два вида:

1. Ответ сотрудника на вопрос HR: `{"uid": "<users.id>", "hr_question": "<строка или пусто>", "answer": "<ответ сотрудника>", "stage": null}`.
   Бот отправляет через `sendHrAnswer` (`bot/src/kafka.js`); сами опросы HR в боте пока не реализованы. Одна реплика — одна оценка.
2. Диалог закрытого тикета (бот: `sendTicketDialog`, при закрытии HR или сотрудником; после переоткрытия и нового закрытия — заново):
   ```json
   {"type": "ticket", "uid": "<users.id>", "ticket_number": 7, "closed_at": "2026-10-24T12:00:00.000Z", "closed_by": "hr",
    "stage": null, "title": "…", "category": null,
    "messages": [{"from": "employee|bot|hr", "text": "…", "at": "…", "attachments": 0}]}
   ```
   Консьюмер собирает транскрипт «Сотрудник: … / Бот: … / HR: …» (последние 12 000 символов), LLM даёт **один вердикт на диалог**
   (`DIALOG_INTRO` + системный промпт): mood/reason/urgent сотрудника по всей переписке. Тикет без реплик сотрудника пропускается.
   Повторная доставка не удваивает счётчики: ключ `ticket:<uid>:<номер>:<closed_at>` уникален в `events`.

### Статистика в Mongo (`STORE_BACKEND=mongo`)
Коллекция `stats` — документ на сотрудника (ключ `uid` = `users.id`, уникальный индекс):
```json
{"stage": null, "total_requests": 3,
 "moods": {"positive": 0, "neutral": 2, "negative": 1, "critical": 0},
 "reasons": {"technical_issues": 1, "conflict": 0, "workload": 0, "work_question": 2, "gratitude": 0, "absence": 0, "finance": 0, "documents": 0},
 "red_flag": false, "last_reason": ""}
```
К документу добавляются `uid` и `updated_at` (ISO, UTC). `red_flag` ставит `urgent`, сбрасывает его только HR (вручную).

Коллекция `events` — история оценок для графиков динамики:
`{uid, ts, mood, reason, urgent, source: "bot_question"|"ticket", ticket_number?, event_key?}`; индексы `(uid, ts)` и уникальный разреженный `event_key`.
Коллекция `locks` — блокировка на пользователя (TTL 30 с), чтобы API и консьюмер не затирали счётчики друг друга.
Бэкенды хранилища: `mongo` (прод, в compose), `redis` (только сводка, без истории), `file` (тесты). Читает `stats`/`events` сервис `hr_analytics/`.

## База знаний

```bash
# загрузить документы в S3 (папку или файл с хоста монтируем в контейнер)
docker compose run --rm -v /путь/к/docs:/import ml python -m app.ingest upload /import
# пересобрать векторную базу из всех документов в S3
docker compose run --rm ml python -m app.ingest build --from-s3
# добавить / удалить один документ (и в S3, и в базе)
docker compose run --rm -v /путь/к/docs:/import ml python -m app.ingest add /import/файл.pdf --s3
docker compose run --rm ml python -m app.ingest delete файл.pdf --s3
```
Поддерживаются `.pdf`, `.txt`, `.md`. Страницы PDF почти без текста распознаются OCR (easyocr, медленно на CPU).
Во время `build` остановите `ml` (`docker compose stop ml`), после — `docker compose up -d ml`: так API увидит новую коллекцию,
и два процесса не пишут в одну Chroma. Индексация текущих 4 PDF на CPU — ~11 минут (146 чанков).

Первый старт `ml` скачивает e5-large (~2.2 ГБ, том `hf_cache`); при медленной сети healthcheck до конца загрузки показывает `unhealthy`.

## Ресурсы

| Компонент | RAM |
|-----------|-----|
| LLM meno-lite-0.1 `Q4_K_M` (Ollama) | ~5–6 ГБ (4.65 ГБ веса + контекст) |
| эмбеддинги `multilingual-e5-large` в `ml` | ~0.9 ГБ в простое (замер), до ~2.5 ГБ под нагрузкой |
| `ml-mood-consumer` | ~0.5 ГБ (эмбеддинги не грузит) |
| остальной стек (Kafka, Postgres, Redis, S3, бот) | ~1.5–2 ГБ |

Итого с LLM нужно **не меньше 12 ГБ RAM** для Docker (рекомендуется 16 ГБ). Легче: `MENO_MODEL=hf.co/bond005/meno-lite-0.1-gguf:IQ3_M`
(3.54 ГБ). Без LLM (`docker compose up` без профиля `llm`) стек занимает ~4–5 ГБ, `/v1/ask` отвечает 503.

## Тесты и отладка

```bash
docker compose run --rm --no-deps ml python -m unittest discover -s tests -v
docker compose run --rm ml python demo.py ask "Сколько дней отпуска мне положено?"
```
