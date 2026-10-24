import json
from typing import Any

import redis

from app.config import settings


def get_redis_client() -> redis.Redis:
    """Создаёт клиент Redis."""
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def check_redis(client: redis.Redis) -> bool:
    """Проверяет доступность Redis."""
    return client.ping()


def get_employee_analytics(
    client: redis.Redis,
    employee_id: str,
) -> dict[str, Any] | None:
    """
    Читает JSON аналитики сотрудника из Redis.

    Ожидаемый ключ:
        onboarding:stats:{employee_id}

    Возвращает None, если ключ отсутствует.
    """
    key = f"{settings.redis_key_prefix}{employee_id}"

    raw_data = client.get(key)

    if raw_data is None:
        return None

    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"В Redis хранится некорректный JSON: {key}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"Ожидался JSON-объект для ключа {key}"
        )

    return data