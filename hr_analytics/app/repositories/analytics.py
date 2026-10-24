from redis import Redis

from app.redis_client import get_employee_analytics


def get_analytics_for_employee(
    client: Redis,
    employee_id: str,
) -> dict | None:
    """
    Возвращает аналитику сотрудника из Redis.

    Если ключ отсутствует, возвращает None.
    """
    return get_employee_analytics(client, employee_id)