from collections import Counter
from datetime import datetime, timezone

from app.repositories.employees import get_all_employees
from app.repositories.analytics import get_analytics_for_employee
from app.redis_client import get_redis_client


ONBOARDING_STAGES = (
    "preparing_for_start_job",
    "first_adaptation_days",
    "beggining_of_adaptation",
    "main_adaptation_part",
    "completion_of_adaptation",
)

MOOD_TYPES = (
    "positive",
    "neutral",
    "negative",
    "critical",
)


def build_hr_daily_report() -> dict:
    """
    Формирует общую сводку по всем сотрудникам для HR.

    Источники:
    - PostgreSQL: список сотрудников и подразделения;
    - Redis: этап адаптации, запросы, настроение и красные флаги.
    """

    employees = get_all_employees()
    redis_client = get_redis_client()

    try:
        redis_client.ping()

        report_employees = []

        stage_counts = Counter()
        request_tag_counts = Counter()
        mood_counts = Counter({mood: 0 for mood in MOOD_TYPES})

        total_requests = 0
        employees_with_analytics = 0
        employees_without_analytics = 0
        employees_with_red_flags = 0

        for employee in employees:
            employee_id = employee["id"]

            analytics = get_analytics_for_employee(
                redis_client,
                employee_id,
            )

            if analytics is None:
                employees_without_analytics += 1

                report_employees.append({
                    **employee,
                    "analytics": None,
                    "analytics_available": False,
                })

                continue

            employees_with_analytics += 1

            stage = analytics.get("stage")

            if stage:
                stage_counts[stage] += 1

            request_count = analytics.get("request_count", 0)

            if isinstance(request_count, (int, float)):
                total_requests += request_count

            request_tags = analytics.get("request_tags") or {}

            for tag, count in request_tags.items():
                if isinstance(count, (int, float)):
                    request_tag_counts[tag] += count

            mood = analytics.get("mood") or {}

            for mood_type in MOOD_TYPES:
                count = mood.get(mood_type, 0)

                if isinstance(count, (int, float)):
                    mood_counts[mood_type] += count

            red_flag = bool(analytics.get("red_flag", False))

            if red_flag:
                employees_with_red_flags += 1

            report_employees.append({
                **employee,
                "analytics": analytics,
                "analytics_available": True,
            })

        # Все известные этапы показываем в отчёте,
        # даже если на каком-то этапе сейчас нет сотрудников.
        normalized_stage_counts = {
            stage: stage_counts.get(stage, 0)
            for stage in ONBOARDING_STAGES
        }

        # Не теряем этапы, которые могли появиться в Redis,
        # но ещё не добавлены в список ONBOARDING_STAGES.
        for stage, count in stage_counts.items():
            if stage not in normalized_stage_counts:
                normalized_stage_counts[stage] = count

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_type": "hr_daily",
            "summary": {
                "total_employees": len(employees),
                "employees_with_analytics": employees_with_analytics,
                "employees_without_analytics": employees_without_analytics,
                "total_requests": total_requests,
                "employees_with_red_flags": employees_with_red_flags,
            },
            "stage_counts": normalized_stage_counts,
            "request_tag_counts": dict(request_tag_counts),
            "mood_counts": dict(mood_counts),
            "employees": report_employees,
        }

    finally:
        redis_client.close()