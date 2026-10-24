import json
import logging
import os

from app.db import check_postgres, get_employee
from app.redis_client import (
    check_redis,
    get_redis_client,
    get_employee_analytics,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    # 1. Проверяем PostgreSQL.
    try:
        if check_postgres():
            logger.info("PostgreSQL: подключение успешно")
    except Exception:
        logger.exception("PostgreSQL: ошибка подключения")
        raise

    # 2. Проверяем Redis.
    client = get_redis_client()

    try:
        if check_redis(client):
            logger.info("Redis: подключение успешно")

        # 3. Если задан ID сотрудника, проверяем интеграцию.
        employee_id = os.getenv("HR_ANALYTICS_TEST_EMPLOYEE_ID")

        if not employee_id:
            logger.info(
                "Проверка данных пропущена: "
                "HR_ANALYTICS_TEST_EMPLOYEE_ID не задан"
            )
            return

        employee = get_employee(employee_id)

        if employee is None:
            logger.warning(
                "Сотрудник с указанным UUID не найден в PostgreSQL"
            )
            return

        logger.info(
            "Сотрудник найден: id=%s, role=%s",
            employee["id"],
            employee["role"],
        )

        analytics = get_employee_analytics(client, employee["id"])

        if analytics is None:
            logger.warning(
                "Для сотрудника пока нет аналитики в Redis"
            )
            return

        # Для локальной отладки выводим структуру JSON.
        # В production персональные данные так не логируем.
        print(json.dumps(analytics, ensure_ascii=False, indent=2))

    finally:
        client.close()


if __name__ == "__main__":
    main()