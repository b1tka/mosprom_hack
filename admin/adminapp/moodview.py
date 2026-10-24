"""Отображение настроения: нормализация документов Mongo и метрики из hr_analytics (analytics.py без копии логики)."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from markupsafe import Markup

from app.analytics import RISK_ORDER, EmployeeRow, build_rows, mood_index  # noqa: F401 — из hr_analytics, см. admin/Dockerfile
from app.models import MOODS, REASONS, Person, Stats

RISK_TITLES = {"high": "высокий", "medium": "повышенный", "low": "норма", "no_data": "нет данных"}
MOOD_TITLES = {"positive": "позитив", "neutral": "нейтрально", "negative": "негатив", "critical": "критично"}
REASON_TITLES = {
    "technical_issues": "технические проблемы", "conflict": "конфликт", "workload": "нагрузка", "work_question": "рабочий вопрос",
    "gratitude": "благодарность", "absence": "отпуск, больничный", "finance": "финансы", "documents": "документы",
}


def _counts(src, keys) -> dict:
    out = {k: 0 for k in keys}
    if isinstance(src, dict):
        for k, v in src.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = int(v)
    return out


def stats_from_doc(doc: dict) -> Stats:
    moods, reasons = _counts(doc.get("moods"), MOODS), _counts(doc.get("reasons"), REASONS)
    total = doc.get("total_requests")
    return Stats(
        uid=str(doc.get("uid")), stage=doc.get("stage"), total=int(total) if isinstance(total, (int, float)) else sum(moods.values()),
        moods=moods, reasons=reasons, red_flag=bool(doc.get("red_flag")), last_reason=str(doc.get("last_reason") or ""),
        updated_at=_dt(doc.get("updated_at")),
    )


def _dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def person_from_row(r: dict) -> Person:
    return Person(id=r["id"], name=r["name"], surname=r["surname"], role=r["role"], tag=r.get("tag"),
                  director_id=r.get("director_id"), email=(r.get("email") or None), max_id=r.get("max_id"))


def weekly_index(events: list[dict], weeks: int = 8) -> list[Optional[float]]:
    """Индекс настроения по неделям (старые -> новые); None, если за неделю оценок не было."""
    now = datetime.now(timezone.utc)
    buckets = [{m: 0 for m in MOODS} for _ in range(weeks)]
    for e in events:
        ts, mood = _dt(e.get("ts")), e.get("mood")
        if ts is None or mood not in MOODS:
            continue
        age = (now - ts) // timedelta(weeks=1)
        if 0 <= age < weeks:
            buckets[weeks - 1 - age][mood] += 1
    return [mood_index(b) for b in buckets]


def spark(values: list[Optional[float]], width: int = 320, height: int = 70) -> Markup:
    """SVG-график индекса (0–100) без внешних библиотек; пропуски недель разрывают линию."""
    n = len(values)
    if not n or all(v is None for v in values):
        return Markup('<p class="muted">Нет данных для графика.</p>')
    pad = 6
    xs = [pad + i * (width - 2 * pad) / max(1, n - 1) for i in range(n)]
    y = lambda v: pad + (100 - v) * (height - 2 * pad) / 100   # noqa: E731
    parts, seg = [], []
    for x, v in zip(xs, values):
        if v is None:
            if len(seg) > 1:
                parts.append(f'<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{" ".join(seg)}"/>')
            seg = []
        else:
            seg.append(f"{x:.1f},{y(v):.1f}")
    if len(seg) > 1:
        parts.append(f'<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{" ".join(seg)}"/>')
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="3" fill="var(--accent)"><title>{v:.0f}</title></circle>' for x, v in zip(xs, values) if v is not None)
    guides = "".join(f'<line x1="{pad}" x2="{width - pad}" y1="{y(g):.1f}" y2="{y(g):.1f}" stroke="var(--line)" stroke-dasharray="3 3"/>' for g in (25, 60, 100))
    return Markup(f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Индекс настроения по неделям">{guides}{"".join(parts)}{dots}</svg>')
