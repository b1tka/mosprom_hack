"""Демо-данные без баз: для `--demo`, предпросмотра отчётов и тестов."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.models import MOODS, REASONS, Event, Person, Stats

D1, D2, D3 = ("10000000-0000-0000-0000-00000000000%d" % i for i in (1, 2, 3))
HR, HR_HEAD = "10000000-0000-0000-0000-0000000000a1", "10000000-0000-0000-0000-0000000000a2"

_EMP = [
    ("Иван", "Козлов", "GTP", D1), ("Анна", "Соколова", "GrPP", D1), ("Дмитрий", "Орлов", "Luzhniki", D1),
    ("Ольга", "Волкова", "GTP", D1), ("Сергей", "Миронов", "GTP", D1),
    ("Мария", "Беляева", "GrPP", D2), ("Артём", "Фёдоров", "GrPP", D2), ("Екатерина", "Лебедева", "Luzhniki", D2),
    ("Павел", "Николаев", "GrPP", D2),
    ("Наталья", "Захарова", "Luzhniki", D3), ("Андрей", "Киселёв", "Luzhniki", D3), ("Юлия", "Романова", "GTP", D3),
]


def people() -> list[Person]:
    out = [
        Person(HR, "Мария", "Иванова", "hr", "GTP", None, "hr@example.com"),
        Person(HR_HEAD, "Елена", "Петрова", "hr_head", None, None, "hr-head@example.com"),
        Person(D1, "Александр", "Смирнов", "director", None, None, "smirnov@example.com"),
        Person(D2, "Ирина", "Кузнецова", "director", None, None, "kuznetsova@example.com"),
        Person(D3, "Виктор", "Попов", "director", None, None, "popov@example.com"),
    ]
    for i, (n, s, tag, d) in enumerate(_EMP, 1):
        out.append(Person(f"20000000-0000-0000-0000-{i:012d}", n, s, "employee", tag, d, f"emp{i}@example.com"))
    return out


# (вес настроений: positive, neutral, negative, critical), основная тема
_PROFILES = [
    ((.15, .35, .30, .20), "workload"), ((.4, .4, .2, 0), "work_question"), ((.2, .3, .35, .15), "conflict"),
    ((.5, .35, .15, 0), "gratitude"), ((.3, .45, .25, 0), "technical_issues"), ((.45, .4, .15, 0), "documents"),
    ((.2, .3, .5, 0), "technical_issues"), ((.5, .4, .1, 0), "work_question"), ((.2, .4, .3, .1), "finance"),
    ((.1, .3, .4, .2), "workload"), ((.4, .4, .2, 0), "absence"), (None, None),   # последний — без обращений
]


def _events(emp_uid: str, idx: int, rnd: random.Random, now: datetime, weeks: int) -> list[Event]:
    weights, main = _PROFILES[idx]
    if not weights:
        return []
    ev = []
    for w in range(weeks):
        # слегка «ухудшаем» последние недели у проблемных профилей, чтобы тренд был живым
        drift = (weights[2] + weights[3]) * max(0, w - weeks + 4) * .15
        for _ in range(rnd.randint(1, 4)):
            wt = [weights[0] - drift / 2, weights[1] - drift / 2, weights[2] + drift / 2, weights[3] + drift / 2]
            wt = [max(.01, x) for x in wt]
            mood = rnd.choices(MOODS, wt)[0]
            reason = main if rnd.random() < .6 else rnd.choice(REASONS)
            ts = now - timedelta(weeks=weeks - 1 - w, days=rnd.randint(0, 5), hours=rnd.randint(0, 10))
            ev.append(Event(emp_uid, ts, mood, reason, urgent=(mood == "critical" and rnd.random() < .5)))
    return ev


def demo_data(now: datetime | None = None, weeks: int = 8, seed: int = 7):
    """-> (people, stats{uid: Stats}, events[Event])"""
    now = now or datetime.now(timezone.utc)
    rnd = random.Random(seed)
    ppl = people()
    emps = [p for p in ppl if p.role == "employee"]
    events: list[Event] = []
    stats: dict[str, Stats] = {}
    for i, p in enumerate(emps):
        ev = _events(p.id, i, rnd, now, weeks)
        events += ev
        if not ev:
            continue
        st = Stats(uid=p.id, stage=None, total=len(ev))     # stage=None: как сейчас отдаёт ML
        for e in ev:
            st.moods[e.mood] += 1
            st.reasons[e.reason] += 1
            if e.urgent:
                st.red_flag = True
                st.last_reason = "Сообщение о перегрузке и признаках выгорания — нужна беседа с HR."
        st.updated_at = max(e.ts for e in ev)
        stats[p.id] = st
    return ppl, stats, events
