# ML-сервис (FastAPI)

Отдельный микросервис, бот обращается к нему по HTTP (`ML_API_URL`, в compose `http://ml:8000`).

## Контракт

`POST /rag`, тело `{"question": "<строка>"}` -> HTTP 200, тело `{"code": 200 | 400, "answer": "<строка>"}`:
- `code: 200` — ответ найден, бот отправляет `answer` пользователю;
- `code: 400` — модель не справилась, бот отвечает своим текстом.

Любой другой HTTP-статус (422 — невалидный запрос, 5xx), таймаут или недоступность бот считает технической ошибкой.

`GET /health` -> `{"status": "ok"}` (healthcheck в compose).

## Заглушка

Сейчас `rag()` в `app/main.py` случайно возвращает 200 (70%) или 400 (30%).
Чтобы подключить модель, замените тело `rag(question) -> tuple[int, str]`; HTTP-обёртку менять не нужно.
Новые зависимости — в `requirements.txt`.

## Запуск

```bash
docker compose up -d --build ml
curl localhost:8000/health
curl -X POST localhost:8000/rag -H 'Content-Type: application/json' -d '{"question":"Где столовая?"}'
```
Документация OpenAPI: http://localhost:8000/docs
