# CLAUDE.md

Бот для мессенджера MAX: онбординг нового сотрудника в компанию. Отвечать на русском.

## Статус
Инфраструктура в Docker и работающий бот (`bot/`, JS, `@maxhub/max-bot-api`). ML-модуль пока не написан.
Подробности: `INFRASTRUCTURE.md`. Документация MAX для JS: https://dev.max.ru/docs/chatbots/bots-coding/js

## Стек
- Postgres 16 — `users`, `role`, `tag`, `invites` (`db/init/*.sql` выполняются только при первом старте тома; для живой БД — `db/migrations/`, вручную).
- Redis 7 (на хосте порт 6380) — состояние диалогов бота (`dialog:<max_id>`), переживает рестарт.
- S3 — OpenMaxIO (форк консоли MinIO), образ `ghcr.io/snazy/maxio-release` (неофициальный; MinIO на Quay/Docker Hub не скачался). Бакет `onboarding-files`.
- Kafka (KRaft, 1 нода) — связь с ML-модулем. Топики `ml.requests` (бот → ML), `ml.responses` (ML → бот).
- Бот — `bot/src`: `index.js` (точка входа, `/start`, `/id`, сообщения -> ML), `invite.js` (`/menu`, `/cancel`, добавление сотрудника, активация по QR), `db.js`, `state.js`, `kafka.js`, `s3.js`, `phone.js`, `config.js`.

## Команды
```bash
cp .env.example .env               # один раз, поменять пароли, указать BOT_TOKEN
docker compose up -d
docker compose ps -a
docker compose up -d --build bot   # после любых правок кода бота
docker compose logs bot
docker compose down -v             # сброс данных, init-скрипты БД выполнятся заново
```
Адреса внутри сети compose: `postgres:5432`, `redis:6379`, `s3:9000`, `kafka:9092`. С хоста: Kafka `localhost:29092`, Redis `localhost:6380`.

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
- В живом MAX не проверено: формат deep-link со `start`, кнопка «Поделиться контактом», показ QR. Список — в `INFRASTRUCTURE.md`.
