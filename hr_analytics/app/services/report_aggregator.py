from collections import Counter
from datetime import datetime, timezone

from app.repositories.employees import get_employees_by_director
from app.repositories.analytics import get_analytics_for_employee
from app.redis_client import get_redis_client


# Список этапов из нашего предварительного контракта.
# Названия пока сохраняем без изменений.
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


def build_director_report(director_id: str) -> dict:
    """
    Собирает аналитический отчёт по сотрудникам руководителя.

    Данные о сотрудниках берутся из PostgreSQL,
    аналитика — из Redis.

    director_id — внутренний UUID руководителя из users.id.
    """

    employees = get_employees_by_director(director_id)

    redis_client = get_redis_client()

    try:
        redis_client.ping()

        report_employees = []

        stage_counts = Counter()
        request_tag_counts = Counter()
        mood_counts = Counter({
            mood: 0 for mood in MOOD_TYPES
        })

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

            # Сохраняем сотрудника в отчёте, даже если
            # аналитики по нему пока нет.
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
            total_requests += request_count

            request_tags = analytics.get("request_tags") or {}

            for tag, count in request_tags.items():
                request_tag_counts[tag] += count

            mood = analytics.get("mood") or {}

            for mood_type in MOOD_TYPES:
                mood_counts[mood_type] += mood.get(mood_type, 0)

            red_flag = bool(analytics.get("red_flag", False))

            if red_flag:
                employees_with_red_flags += 1

            report_employees.append({
                **employee,
                "analytics": analytics,
                "analytics_available": True,
            })

        # Включаем все известные этапы в результат,
        # даже если на каком-то из них сейчас нет сотрудников.
        normalized_stage_counts = {
            stage: stage_counts.get(stage, 0)
            for stage in ONBOARDING_STAGES
        }

        # На случай, если в Redis появится новый этап,
        # которого ещё нет в нашем списке.
        for stage, count in stage_counts.items():
            if stage not in normalized_stage_counts:
                normalized_stage_counts[stage] = count

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "director_id": director_id,

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