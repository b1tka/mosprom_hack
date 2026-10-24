import json
import os

import redis
from dotenv import load_dotenv


load_dotenv()


REDIS_HOST = os.getenv("HR_ANALYTICS_REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("HR_ANALYTICS_REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("HR_ANALYTICS_REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("HR_ANALYTICS_REDIS_PASSWORD") or None

KEY_PREFIX = os.getenv(
    "HR_ANALYTICS_REDIS_KEY_PREFIX",
    "onboarding:stats:",
)


# Ключи соответствуют users.id из seed.sql.
MOCK_ANALYTICS = {
    "20000000-0000-0000-0000-000000000001": {
        "stage": "first_adaptation_days",
        "request_count": 12,
        "request_tags": {
            "technical_issues": 4,
            "work_question": 5,
            "documents": 2,
            "gratitude": 1,
        },
        "mood": {
            "positive": 5,
            "neutral": 4,
            "negative": 2,
            "critical": 1,
        },
        "red_flag": True,
        "reason": "Повторяющиеся технические проблемы и негативные оценки",
    },

    "20000000-0000-0000-0000-000000000002": {
        "stage": "beggining_of_adaptation",
        "request_count": 8,
        "request_tags": {
            "technical_issues": 1,
            "work_question": 3,
            "workload": 3,
            "gratitude": 1,
        },
        "mood": {
            "positive": 3,
            "neutral": 3,
            "negative": 2,
            "critical": 0,
        },
        "red_flag": False,
        "reason": None,
    },

    "20000000-0000-0000-0000-000000000003": {
        "stage": "main_adaptation_part",
        "request_count": 17,
        "request_tags": {
            "technical_issues": 2,
            "conflict": 3,
            "workload": 7,
            "work_question": 3,
            "absence": 1,
            "finance": 1,
        },
        "mood": {
            "positive": 2,
            "neutral": 5,
            "negative": 7,
            "critical": 3,
        },
        "red_flag": True,
        "reason": "Высокая нагрузка, признаки конфликта и устойчивое негативное настроение",
    },
}


def main():
    client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )

    try:
        client.ping()

        for employee_id, analytics in MOCK_ANALYTICS.items():
            key = f"{KEY_PREFIX}{employee_id}"

            client.set(
                key,
                json.dumps(
                    analytics,
                    ensure_ascii=False,
                ),
            )

            print(f"Записана заглушка: {key}")

        print("Все тестовые данные Redis записаны.")

    finally:
        client.close()


if __name__ == "__main__":
    main()