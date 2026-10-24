import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    postgres_host: str = os.getenv(
        "HR_ANALYTICS_POSTGRES_HOST", "localhost"
    )
    postgres_port: int = int(os.getenv(
        "HR_ANALYTICS_POSTGRES_PORT", "5432"
    ))
    postgres_db: str = os.getenv(
        "HR_ANALYTICS_POSTGRES_DB", "onboarding"
    )
    postgres_user: str = os.getenv(
        "HR_ANALYTICS_POSTGRES_USER", "postgres"
    )
    postgres_password: str = os.getenv(
        "HR_ANALYTICS_POSTGRES_PASSWORD", ""
    )

    redis_host: str = os.getenv(
        "HR_ANALYTICS_REDIS_HOST", "localhost"
    )
    redis_port: int = int(os.getenv(
        "HR_ANALYTICS_REDIS_PORT", "6379"
    ))
    redis_db: int = int(os.getenv(
        "HR_ANALYTICS_REDIS_DB", "0"
    ))
    redis_password: str | None = (
        os.getenv("HR_ANALYTICS_REDIS_PASSWORD") or None
    )

    redis_key_prefix: str = os.getenv(
        "HR_ANALYTICS_REDIS_KEY_PREFIX",
        "onboarding:stats:",
    )


settings = Settings()