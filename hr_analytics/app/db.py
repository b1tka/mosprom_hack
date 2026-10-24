import psycopg

from app.config import settings


def get_connection():
    """Создаёт подключение к PostgreSQL."""
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=5,
    )


def check_postgres() -> bool:
    """Проверяет доступность PostgreSQL."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1


def get_employee(employee_id: str) -> dict | None:
    """
    Получает сотрудника, его роль и руководителя по UUID сотрудника.

    Возвращает None, если сотрудник не найден.
    """
    query = """
        SELECT
            u.id,
            u.name,
            u.surname,
            u.max_id,
            r.name AS role,
            u.director_id,
            d.name AS director_name,
            d.surname AS director_surname
        FROM users AS u
        JOIN role AS r
            ON r.id = u.role_id
        LEFT JOIN users AS d
            ON d.id = u.director_id
        WHERE u.id = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (employee_id,))
            row = cur.fetchone()

            if row is None:
                return None

            columns = [desc.name for desc in cur.description]
            result = dict(zip(columns, row))

            # UUID преобразуем в строку для дальнейшей работы с Redis.
            result["id"] = str(result["id"])
            if result["director_id"] is not None:
                result["director_id"] = str(result["director_id"])

            return result