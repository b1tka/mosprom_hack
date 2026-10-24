"""Сборка отчётов по ролям из PostgreSQL (люди) + MongoDB (статистика)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from app import charts
from app.analytics import (build_rows, group_moods, summarize, trend_delta, weekly_trend, EmployeeRow, Summary)
from app.document import Report
from app.labels import REASON_LABELS, RISK_ADVICE, RISK_LABELS, stage_label
from app.models import (ROLE_DIRECTOR, ROLE_EMPLOYEE, ROLE_HR, ROLE_HR_HEAD, Event, Person, Stats)

log = logging.getLogger(__name__)
RISK_TONE = {"high": "bad", "medium": "warn", "low": "good", "no_data": "neutral"}


@dataclass
class Data:
    people: list[Person]
    stats: dict[str, Stats]
    events: list[Event]
    now: datetime
    trend_weeks: int = 8

    @property
    def by_id(self) -> dict[str, Person]:
        return {p.id: p for p in self.people}

    @property
    def employees(self) -> list[Person]:
        return [p for p in self.people if p.role == ROLE_EMPLOYEE]


def _d(now: datetime) -> str:
    return now.strftime("%d.%m.%Y")


def _fmt_idx(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0f}"


def _idx_tone(v: Optional[float]) -> str:
    return "neutral" if v is None else "good" if v >= 65 else "warn" if v >= 45 else "bad"


def _kpis(s: Summary, extra: Optional[list] = None) -> dict:
    items = [
        {"label": "Новичков", "value": s.employees, "hint": f"с обращениями: {s.with_data}"},
        {"label": "Красные флаги", "value": s.red_flags, "tone": "bad" if s.red_flags else "good",
         "hint": "требуют внимания HR" if s.red_flags else "нет"},
        {"label": "Индекс настроения", "value": _fmt_idx(s.index), "tone": _idx_tone(s.index), "hint": "0–100, нейтрально = 60"},
        {"label": "Сообщений", "value": s.requests, "hint": "классифицировано ИИ"},
    ]
    return {"type": "kpis", "items": (extra or items)}


def _employee_table(rows: list[EmployeeRow], with_director: bool, with_tag: bool = True, limit: Optional[int] = None) -> dict:
    # колонку «Этап» показываем, только если ML уже отдаёт этапы (сейчас stage = null)
    with_stage = any(r.stats and r.stats.stage for r in rows)
    cols = ["Сотрудник"] + (["Подразделение"] if with_tag else []) + (["Руководитель"] if with_director else []) + \
           (["Этап"] if with_stage else []) + ["Сообщ.", "Настроение", "Главная тема", "Статус"]
    out = []
    for r in rows[:limit]:
        cells = [r.person.full_name]
        if with_tag:
            cells.append(r.person.tag or "—")
        if with_director:
            cells.append(r.director.short_name if r.director else "—")
        if with_stage:
            cells.append(stage_label(r.stats.stage if r.stats else None))
        cells += [
            r.total,
            f"{_fmt_idx(r.index)}" if r.index is not None else "—",
            REASON_LABELS.get(r.topic, "—") if r.topic else "—",
            {"text": RISK_LABELS[r.risk], "tone": RISK_TONE[r.risk]},
        ]
        out.append(cells)
    return {"type": "table", "columns": cols, "rows": out}


def _red_flag_callout(rows: list[EmployeeRow], with_director: bool) -> Optional[dict]:
    flagged = [r for r in rows if r.stats and r.stats.red_flag]
    if not flagged:
        return None
    items = []
    for r in flagged:
        who = r.person.full_name + (f" ({r.person.tag})" if r.person.tag else "")
        reason = r.stats.last_reason or "причина не указана"
        boss = f" Руководитель: {r.director.short_name}." if with_director and r.director else ""
        items.append(f"{who} — {reason}{boss}")
    return {"type": "callout", "tone": "bad", "title": f"Красные флаги — {len(flagged)}", "items": items}


def _stage_section(rep: Report, s: Summary) -> None:
    if s.stages_known:
        rep.add_chart("stages", charts.stage_chart(s.stage_series()), "Этапы онбординга", "Сколько новичков на каждом этапе")
    else:
        rep.sections.append({"type": "text", "text": "Этап онбординга пока не передаётся из ИИ-модуля — блок появится автоматически, когда данные станут доступны."})


def _trend_section(rep: Report, d: Data, uids: set[str], title: str = "Динамика настроения по неделям") -> None:
    if not d.events:
        return
    tr = weekly_trend(d.events, uids, d.now, d.trend_weeks)
    delta = trend_delta(tr)
    cap = "Индекс настроения за неделю (0–100)"
    if delta is not None:
        cap += f" · к предыдущей неделе: {delta:+.0f}"
    rep.add_chart("trend", charts.trend_chart(tr), title, cap)


# ---------------------------------------------------------------- роли

def build_hr_report(d: Data, recipient: Person) -> Report:
    """HR-менеджер: сводка по всем новичкам — этапы, темы трудностей, красные флаги."""
    rows = build_rows(d.employees, d.stats, d.by_id)
    s = summarize(rows)
    rep = Report("hr", "Ежедневная сводка по онбордингу", f"HR-менеджер · {_d(d.now)}",
                 f"Онбординг · сводка для HR · {_d(d.now)}",
                 f"Красных флагов: {s.red_flags}. Индекс настроения: {_fmt_idx(s.index)}",
                 f"hr_daily_{d.now:%Y%m%d}", recipient_name=recipient.name)
    rep.sections.append(_kpis(s))
    co = _red_flag_callout(rows, with_director=True)
    if co:
        rep.sections.append(co)
    else:
        rep.sections.append({"type": "callout", "tone": "good", "title": "Красных флагов нет", "items": ["Срочных эскалаций по новичкам сегодня нет."]})
    rep.sections.append({"type": "heading", "text": "Настроение и темы"})
    rep.add_chart("mood", charts.mood_donut(s.moods), "Тональность сообщений новичков")
    rep.add_chart("reasons", charts.reasons_chart(s.reasons), "С чем обращаются новички", "Количество сообщений по темам")
    _stage_section(rep, s)
    _trend_section(rep, d, {r.person.id for r in rows})
    rep.sections.append({"type": "heading", "text": "Новички по приоритету внимания"})
    rep.sections.append(_employee_table(rows, with_director=True))
    return rep


def build_director_report(d: Data, recipient: Person) -> Optional[Report]:
    """Руководитель подразделения: статус адаптации его новых подчинённых + кого пора встретить лично."""
    mine = [p for p in d.employees if p.director_id == recipient.id]
    if not mine:
        return None
    rows = build_rows(mine, d.stats, d.by_id)
    s = summarize(rows)
    meet = [r for r in rows if r.risk in ("high", "medium")]
    silent = [r for r in rows if r.risk == "no_data"]
    rep = Report("director", "Адаптация ваших новых сотрудников", f"{recipient.full_name} · {_d(d.now)}",
                 f"Онбординг · ваши новички · {_d(d.now)}",
                 (f"Нужна личная встреча: {len(meet)}" if meet else "Все новички в порядке"),
                 f"director_{d.now:%Y%m%d}_{recipient.surname}", recipient_name=recipient.name)
    rep.sections.append({"type": "kpis", "items": [
        {"label": "Новичков", "value": s.employees},
        {"label": "Нужна встреча", "value": len(meet), "tone": "bad" if any(r.risk == 'high' for r in meet) else ("warn" if meet else "good"),
         "hint": "высокий и повышенный риск"},
        {"label": "Индекс настроения", "value": _fmt_idx(s.index), "tone": _idx_tone(s.index), "hint": "0–100"},
        {"label": "Без обращений", "value": len(silent), "hint": "нет данных"},
    ]})
    if meet:
        rep.sections.append({"type": "callout", "tone": "bad" if any(r.risk == "high" for r in meet) else "warn",
                             "title": "Рекомендуем провести личную встречу",
                             "items": [f"{r.person.full_name} — {RISK_ADVICE[r.risk].lower()}"
                                       + (f". Тема: {REASON_LABELS[r.topic].lower()}" if r.topic else "") for r in meet]})
    else:
        rep.sections.append({"type": "callout", "tone": "good", "title": "Всё спокойно",
                             "items": ["По вашим новичкам нет сигналов, требующих личной встречи."]})
    rep.sections.append({"type": "heading", "text": "Статус каждого сотрудника"})
    rep.add_chart("employees", charts.employees_mood_chart(rows), "Настроение по сотрудникам", "Доли сообщений по тональности")
    rep.sections.append(_employee_table(rows, with_director=False))
    _stage_section(rep, s)
    _trend_section(rep, d, {r.person.id for r in rows}, "Динамика настроения вашей группы")
    return rep


def build_hr_head_report(d: Data, recipient: Person) -> Report:
    """Руководитель управления по подбору и развитию: еженедельный дайджест «Настроения в коллективе новичков»."""
    rows = build_rows(d.employees, d.stats, d.by_id)
    s = summarize(rows)
    rep = Report("hr_head", "Настроения в коллективе новичков", f"Еженедельный дайджест · неделя до {_d(d.now)}",
                 f"Онбординг · настроения в коллективе новичков · {_d(d.now)}",
                 f"Зон риска: {s.high + s.medium}. Индекс настроения: {_fmt_idx(s.index)}",
                 f"hr_head_weekly_{d.now:%Y%m%d}", recipient_name=recipient.name)
    by_tag = group_moods(rows, lambda r: r.person.tag)
    by_dir = group_moods(rows, lambda r: r.director.full_name if r.director else None)

    extra = [
        {"label": "Новичков", "value": s.employees, "hint": f"с обращениями: {s.with_data}"},
        {"label": "Индекс настроения", "value": _fmt_idx(s.index), "tone": _idx_tone(s.index), "hint": "0–100, нейтрально = 60"},
        {"label": "В зоне риска", "value": s.high + s.medium, "tone": "bad" if s.high else ("warn" if s.medium else "good"),
         "hint": f"высокий: {s.high}, повышенный: {s.medium}"},
        {"label": "Красные флаги", "value": s.red_flags, "tone": "bad" if s.red_flags else "good"},
    ]
    rep.sections.append({"type": "kpis", "items": extra})

    # --- зоны риска: словами, чтобы можно было прочитать за 10 секунд
    zone = []
    tot = sum(s.reasons.values())
    probs = sorted(((k, v) for k, v in s.reasons.items() if k in {"technical_issues", "conflict", "workload", "absence", "finance", "documents"} and v),
                   key=lambda x: -x[1])
    for k, v in probs[:3]:
        zone.append(f"Тема «{REASON_LABELS[k]}» — {v} сообщ. ({v / tot:.0%} всех обращений)")
    worst = sorted((g for g in by_tag.items() if g[1]["total"]), key=lambda g: -g[1]["neg_share"])
    if worst and worst[0][1]["neg_share"] > 0:
        n, g = worst[0]
        zone.append(f"Подразделение {n}: {g['neg_share']:.0%} сообщений негативные или критические")
    worst_d = sorted((g for g in by_dir.items() if g[1]["total"]), key=lambda g: -g[1]["neg_share"])
    if worst_d and worst_d[0][1]["neg_share"] > 0:
        n, g = worst_d[0]
        zone.append(f"Группа руководителя {n}: {g['neg_share']:.0%} негатива, красных флагов у новичков — {g['high']}")
    if zone:
        rep.sections.append({"type": "callout", "tone": "warn" if not s.high else "bad", "title": "Зоны риска", "items": zone})

    co = _red_flag_callout(rows, with_director=True)
    if co:
        rep.sections.append(co)

    rep.sections.append({"type": "heading", "text": "Картина по коллективу"})
    rep.add_chart("mood", charts.mood_donut(s.moods), "Тональность сообщений")
    _trend_section(rep, d, {r.person.id for r in rows})
    rep.add_chart("reasons", charts.reasons_chart(s.reasons), "Кластеры обращений", "О чём говорят новички")
    rep.add_chart("tags", charts.groups_mood_chart(by_tag), "По подразделениям", "Сначала подразделения с наибольшей долей негатива")
    rep.add_chart("dirs", charts.groups_mood_chart(by_dir), "По руководителям", "Новички по руководителям")
    _stage_section(rep, s)
    rep.sections.append({"type": "heading", "text": "Новички, требующие внимания"})
    attention = [r for r in rows if r.risk in ("high", "medium")]
    if attention:
        rep.sections.append(_employee_table(attention, with_director=True))
    else:
        rep.sections.append({"type": "text", "text": "Новичков в зоне риска нет."})
    return rep


BUILDERS = {ROLE_HR: build_hr_report, ROLE_DIRECTOR: build_director_report, ROLE_HR_HEAD: build_hr_head_report}


def build_for_role(role: str, d: Data) -> list[tuple[Person, Report]]:
    """Личный отчёт каждому получателю роли (у кого есть e-mail — решает отправитель)."""
    out = []
    for p in d.people:
        if p.role != role:
            continue
        rep = BUILDERS[role](d, p)
        if rep is None:
            log.info("Для %s нет данных для отчёта — пропуск", p.full_name)
            continue
        out.append((p, rep))
    return out
