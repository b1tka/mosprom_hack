import os
from contextlib import contextmanager
from typing import Any, Generator

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


# Загружаем переменные окружения из .env
load_dotenv()


def get_db_config() -> dict[str, Any]:
    """
    Возвращает параметры подключения к PostgreSQL.

    Все параметры задаются через .env.
    """

    return {
        "host": os.getenv("HR_ANALYTICS_DB_HOST", "localhost"),
        "port": int(os.getenv("HR_ANALYTICS_DB_PORT", "5432")),
        "dbname": os.getenv("HR_ANALYTICS_DB_NAME", "onboarding"),
        "user": os.getenv("HR_ANALYTICS_DB_USER", "postgres"),
        "password": os.getenv("HR_ANALYTICS_DB_PASSWORD", ""),
    }


@contextmanager
def get_connection() -> Generator[psycopg.Connection, None, None]:
    """
    Создаёт соединение с PostgreSQL.

    После завершения работы с соединением оно закрывается.
    При возникновении ошибки транзакция откатывается.
    """

    config = get_db_config()

    with psycopg.connect(
        **config,
        row_factory=dict_row,
        connect_timeout=5,
    ) as connection:
        yield connection


def check_connection() -> bool:
    """
    Проверяет доступность PostgreSQL.

    Возвращает True, если соединение успешно.
    При ошибке подключения выбрасывает исключение.
    """

    with get_connection() as connection:
        connection.execute("SELECT 1")

    return True


def get_all_users() -> list[dict[str, Any]]:
    """
    Возвращает список пользователей с названиями ролей и тегов.
    """

    query = """
        SELECT
            u.id,
            u.name,
            u.surname,
            u.max_id,
            u.role_id,
            r.name AS role_name,
            u.tag_id,
            t.name AS tag_name,
            u.director_id,
            u.created_at,
            u.updated_at
        FROM users AS u
        JOIN role AS r
            ON r.id = u.role_id
        LEFT JOIN tag AS t
            ON t.id = u.tag_id
        ORDER BY u.surname, u.name;
    """

    with get_connection() as connection:
        rows = connection.execute(query).fetchall()

    return [dict(row) for row in rows]


def get_user_by_max_id(max_id: int) -> dict[str, Any] | None:
    """
    Находит пользователя по его идентификатору в MAX.

    Возвращает словарь с данными пользователя
    или None, если пользователь не найден.
    """

    query = """
        SELECT
            u.id,
            u.name,
            u.surname,
            u.max_id,
            r.name AS role_name,
            t.name AS tag_name,
            u.director_id
        FROM users AS u
        JOIN role AS r
            ON r.id = u.role_id
        LEFT JOIN tag AS t
            ON t.id = u.tag_id
        WHERE u.max_id = %s;
    """

    with get_connection() as connection:
        row = connection.execute(query, (max_id,)).fetchone()

    return dict(row) if row is not None else None


def get_employees_for_director(
    director_id: str,
) -> list[dict[str, Any]]:
    """
    Возвращает сотрудников конкретного руководителя.

    director_id — UUID руководителя из таблицы users.
    В выборку попадают только пользователи с ролью employee.
    """

    query = """
        SELECT
            u.id,
            u.name,
            u.surname,
            u.max_id,
            u.role_id,
            r.name AS role_name,
            u.tag_id,
            t.name AS tag_name,
            u.director_id,
            u.created_at,
            u.updated_at
        FROM users AS u
        JOIN role AS r
            ON r.id = u.role_id
        LEFT JOIN tag AS t
            ON t.id = u.tag_id
        WHERE u.director_id = %s
          AND r.name = 'employee'
        ORDER BY u.surname, u.name;
    """

    with get_connection() as connection:
        rows = connection.execute(
            query,
            (director_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_users_by_role(
    role_name: str,
) -> list[dict[str, Any]]:
    """
    Возвращает всех пользователей с указанной ролью.

    Например:
        get_users_by_role("hr")
        get_users_by_role("director")
    """

    query = """
        SELECT
            u.id,
            u.name,
            u.surname,
            u.max_id,
            r.name AS role_name,
            t.name AS tag_name,
            u.director_id
        FROM users AS u
        JOIN role AS r
            ON r.id = u.role_id
        LEFT JOIN tag AS t
            ON t.id = u.tag_id
        WHERE r.name = %s
        ORDER BY u.surname, u.name;
    """

    with get_connection() as connection:
        rows = connection.execute(
            query,
            (role_name,),
        ).fetchall()

    return [dict(row) for row in rows]