"""Расчёт метрик: чистые функции без обращений к базам и почте."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from app.models import MOODS, PROBLEM_REASONS, REASONS, STAGE_ORDER, Event, Person, Stats

# вес настроения для «индекса настроения» 0–100
MOOD_SCORE = {"positive": 100, "neutral": 60, "negative": 25, "critical": 0}
RISK_ORDER = {"high": 0, "medium": 1, "no_data": 2, "low": 3}


def mood_index(moods: dict) -> Optional[float]:
    n = sum(moods.get(m, 0) for m in MOODS)
    if not n:
        return None
    return sum(MOOD_SCORE[m] * moods.get(m, 0) for m in MOODS) / n


def negative_share(moods: dict) -> float:
    n = sum(moods.get(m, 0) for m in MOODS)
    return (moods.get("negative", 0) + moods.get("critical", 0)) / n if n else 0.0


def assess_risk(st: Optional[Stats]) -> str:
    if st is None or st.total == 0:
        return "no_data"
    if st.red_flag or st.moods.get("critical", 0) > 0:
        return "high"
    if st.total >= 3 and negative_share(st.moods) >= 0.4:
        return "medium"
    return "low"


def top_problem(reasons: dict) -> Optional[str]:
    """Главная проблемная тема сотрудника; если проблем нет — самая частая тема."""
    problems = {r: reasons.get(r, 0) for r in PROBLEM_REASONS if reasons.get(r, 0) > 0}
    pool = problems or {r: c for r, c in reasons.items() if c > 0}
    return max(pool, key=pool.get) if pool else None


@dataclass
class EmployeeRow:
    person: Person
    stats: Optional[Stats]
    director: Optional[Person] = None
    risk: str = "no_data"
    index: Optional[float] = None
    neg_share: float = 0.0
    topic: Optional[str] = None

    @property
    def total(self) -> int:
        return self.stats.total if self.stats else 0

    @property
    def moods(self) -> dict:
        return self.stats.moods if self.stats else {m: 0 for m in MOODS}


def build_rows(employees: Iterable[Person], stats: dict[str, Stats], people_by_id: dict[str, Person]) -> list[EmployeeRow]:
    rows = []
    for p in employees:
        st = stats.get(p.id)
        rows.append(EmployeeRow(
            person=p, stats=st, director=people_by_id.get(p.director_id) if p.director_id else None,
            risk=assess_risk(st), index=mood_index(st.moods) if st else None,
            neg_share=negative_share(st.moods) if st else 0.0, topic=top_problem(st.reasons) if st else None,
        ))
    rows.sort(key=lambda r: (RISK_ORDER[r.risk], -r.neg_share, r.person.surname))
    return rows


@dataclass
class Summary:
    employees: int = 0
    with_data: int = 0
    without_data: int = 0
    requests: int = 0
    red_flags: int = 0
    high: int = 0
    medium: int = 0
    index: Optional[float] = None
    moods: dict = field(default_factory=lambda: {m: 0 for m in MOODS})
    reasons: dict = field(default_factory=lambda: {r: 0 for r in REASONS})
    stages: Counter = field(default_factory=Counter)

    @property
    def stages_known(self) -> bool:
        return any(k for k in self.stages if k)

    def stage_series(self) -> list[tuple[Optional[str], int]]:
        """Этапы в порядке онбординга (все, даже пустые) + «не определён», если он есть."""
        out = [(s, self.stages.get(s, 0)) for s in STAGE_ORDER]
        for s, c in self.stages.items():
            if s and s not in STAGE_ORDER:
                out.append((s, c))
        if self.stages.get(None):
            out.append((None, self.stages[None]))
        return out


def summarize(rows: list[EmployeeRow]) -> Summary:
    s = Summary(employees=len(rows))
    for r in rows:
        s.stages[r.stats.stage if r.stats else None] += 1
        if r.stats is None or r.stats.total == 0:
            s.without_data += 1
            continue
        s.with_data += 1
        s.requests += r.stats.total
        for m in MOODS:
            s.moods[m] += r.stats.moods.get(m, 0)
        for k, v in r.stats.reasons.items():
            s.reasons[k] = s.reasons.get(k, 0) + v
    s.red_flags = sum(1 for r in rows if r.stats and r.stats.red_flag)
    s.high = sum(1 for r in rows if r.risk == "high")
    s.medium = sum(1 for r in rows if r.risk == "medium")
    s.index = mood_index(s.moods)
    return s


def group_moods(rows: list[EmployeeRow], key) -> dict[str, dict]:
    """Суммарные настроения по группам (подразделение/руководитель) + число людей."""
    groups: dict[str, dict] = defaultdict(lambda: {"moods": {m: 0 for m in MOODS}, "people": 0, "high": 0})
    for r in rows:
        g = groups[key(r) or "Не указано"]
        g["people"] += 1
        g["high"] += r.risk == "high"
        for m in MOODS:
            g["moods"][m] += r.moods.get(m, 0)
    for g in groups.values():
        g["index"] = mood_index(g["moods"])
        g["neg_share"] = negative_share(g["moods"])
        g["total"] = sum(g["moods"].values())
    return dict(groups)


# ---------- динамика по историческим событиям (если есть коллекция событий) ----------

def week_start(dt: datetime) -> datetime:
    d = dt - timedelta(days=dt.weekday())
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def weekly_trend(events: Iterable[Event], uids: set[str], now: datetime, weeks: int) -> list[dict]:
    first = week_start(now) - timedelta(weeks=weeks - 1)
    buckets: dict[datetime, dict] = {
        first + timedelta(weeks=i): {"moods": {m: 0 for m in MOODS}, "urgent": 0, "n": 0} for i in range(weeks)
    }
    for e in events:
        if e.uid not in uids or not e.mood:
            continue
        b = buckets.get(week_start(e.ts.astimezone(now.tzinfo)))
        if b:
            b["moods"][e.mood] += 1
            b["urgent"] += e.urgent
            b["n"] += 1
    return [{"week": w, "index": mood_index(b["moods"]), "n": b["n"], "urgent": b["urgent"], "moods": b["moods"]}
            for w, b in sorted(buckets.items())]


def trend_delta(trend: list[dict]) -> Optional[float]:
    """Изменение индекса настроения: последняя неделя с данными против предыдущей с данными."""
    pts = [t["index"] for t in trend if t["index"] is not None]
    return pts[-1] - pts[-2] if len(pts) >= 2 else None
