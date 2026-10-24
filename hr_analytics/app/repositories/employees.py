from app.db import get_connection


def get_employees_by_director(director_id: str) -> list[dict]:
    """
    Возвращает всех сотрудников, непосредственно подчинённых руководителю.

    director_id — внутренний UUID пользователя из users.id,
    а не идентификатор пользователя в MAX.
    """
    query = """
        SELECT
            u.id,
            u.name,
            u.surname,
            u.max_id,
            u.director_id,
            r.name AS role,
            t.name AS department
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
        with connection.cursor() as cursor:
            cursor.execute(query, (director_id,))

            column_names = [
                column.name
                for column in cursor.description
            ]

            rows = cursor.fetchall()

    employees = []

    for row in rows:
        employee = dict(zip(column_names, row))

        # Преобразуем UUID в строку, чтобы его можно было
        # использовать при формировании Redis-ключа.
        employee["id"] = str(employee["id"])
        employee["director_id"] = str(employee["director_id"])

        employees.append(employee)

    return employees

def get_all_employees() -> list[dict]:
    """
    Возвращает всех сотрудников компании с ролью employee.

    Используется для общей аналитики HR, не ограниченной
    конкретным руководителем.
    """
    query = """
        SELECT
            u.id,
            u.name,
            u.surname,
            u.max_id,
            u.director_id,
            r.name AS role,
            t.name AS department
        FROM users AS u
        JOIN role AS r ON r.id = u.role_id
        LEFT JOIN tag AS t ON t.id = u.tag_id
        WHERE r.name = 'employee'
        ORDER BY u.surname, u.name;
    """

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            column_names = [column.name for column in cursor.description]
            rows = cursor.fetchall()

    employees = []

    for row in rows:
        employee = dict(zip(column_names, row))

        # Приводим UUID к строке, чтобы использовать его как ключ Redis.
        employee["id"] = str(employee["id"])

        if employee["director_id"] is not None:
            employee["director_id"] = str(employee["director_id"])

        employees.append(employee)

    return employees