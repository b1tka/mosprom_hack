from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

MOODS = ("positive", "neutral", "negative", "critical")
REASONS = (
    "technical_issues", "conflict", "workload", "work_question",
    "gratitude", "absence", "finance", "documents",
)
# «проблемные» темы: именно по ним HR ищет трудности новичков
PROBLEM_REASONS = ("technical_issues", "conflict", "workload", "absence", "finance", "documents")

STAGE_ORDER = (
    "preparing_for_start_job",
    "first_adaptation_days",
    "beggining_of_adaptation",   # написание сохранено как в контракте ML-команды
    "main_adaptation_part",
    "completion_of_adaptation",
)

ROLE_EMPLOYEE, ROLE_DIRECTOR, ROLE_HR, ROLE_HR_HEAD = "employee", "director", "hr", "hr_head"


@dataclass
class Person:
    """Строка таблицы users (PostgreSQL) с названиями роли и тега."""
    id: str
    name: str
    surname: str
    role: str
    tag: Optional[str] = None
    director_id: Optional[str] = None
    email: Optional[str] = None
    max_id: Optional[int] = None

    @property
    def full_name(self) -> str:
        return f"{self.surname} {self.name}".strip()

    @property
    def short_name(self) -> str:
        return f"{self.surname} {self.name[:1]}." if self.name else self.surname


@dataclass
class Stats:
    """Нормализованная статистика одного сотрудника из MongoDB."""
    uid: str
    stage: Optional[str] = None
    total: int = 0
    moods: dict = field(default_factory=lambda: {m: 0 for m in MOODS})
    reasons: dict = field(default_factory=lambda: {r: 0 for r in REASONS})
    red_flag: bool = False
    last_reason: str = ""
    updated_at: Optional[datetime] = None


@dataclass
class Event:
    """Одна классифицированная реплика (необязательная коллекция событий)."""
    uid: str
    ts: datetime
    mood: Optional[str] = None
    reason: Optional[str] = None
    urgent: bool = False
