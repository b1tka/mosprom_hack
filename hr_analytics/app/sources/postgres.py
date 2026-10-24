"""ФИО, роли, теги и e-mail — из PostgreSQL (таблицы users / role / tag)."""
from __future__ import annotations

from typing import Any, Callable, Optional

from app.config import Settings
from app.models import Person

QUERY = """
SELECT u.id::text AS id, u.name, u.surname, r.name AS role, t.name AS tag,
       u.director_id::text AS director_id, u.email, u.max_id
FROM users AS u
JOIN role AS r ON r.id = u.role_id
LEFT JOIN tag AS t ON t.id = u.tag_id
ORDER BY u.surname, u.name
"""


class PeopleSource:
    def __init__(self, settings: Settings, connect: Optional[Callable[..., Any]] = None):
        self.s = settings
        if connect is None:
            import psycopg  # psycopg 3

            connect = psycopg.connect
        self._connect = connect

    def ping(self) -> bool:
        with self._connect(self.s.database_url, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return True

    def load_people(self) -> list[Person]:
        with self._connect(self.s.database_url, connect_timeout=5) as conn:
            cur = conn.execute(QUERY)
            cols = [c.name if hasattr(c, "name") else c[0] for c in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return [
            Person(
                id=str(r["id"]), name=r["name"], surname=r["surname"], role=r["role"],
                tag=r.get("tag"), director_id=r.get("director_id"),
                email=(r.get("email") or "").strip() or None, max_id=r.get("max_id"),
            )
            for r in rows
        ]
