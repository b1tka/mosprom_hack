"""Настройки тестовой админки. Всё из переменных окружения (в compose их задаёт docker-compose.yml)."""
import os


def _s(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


ACCESS = _s("ADMIN_ACCESS", "off")                 # off | hr | anyone
SESSION_SECRET = _s("SESSION_SECRET")
API_TOKEN = _s("ADMIN_API_TOKEN")                  # токен внутреннего HTTP бота
ALLOWED_HOSTS = [h.strip() for h in _s("ADMIN_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

DATABASE_URL = _s("DATABASE_URL", "postgresql://onboarding:change_me@localhost:5432/onboarding")
MONGO_URL = _s("MONGO_URL", "mongodb://onboarding:change_me_mongo@localhost:27018/onboarding")   # база — из пути URL
REDIS_URL = _s("REDIS_URL", "redis://localhost:6380")
ML_URL = _s("ML_URL", "http://localhost:8000")
BOT_URL = _s("BOT_URL", "http://bot:3001")
OLLAMA_URL = _s("OLLAMA_URL", "http://ollama:11434")
KAFKA_BOOTSTRAP = _s("KAFKA_BOOTSTRAP", "localhost:29092")
# группа:топик — чтобы показать, сколько сообщений лежит в топике у группы без коммитов
KAFKA_GROUPS = dict(g.split(":", 1) for g in _s("KAFKA_GROUPS", "corsik-mood:onboarding.answers,onboarding-bot-alerts:onboarding.alerts").split(",") if ":" in g)
S3_ENDPOINT = _s("S3_ENDPOINT", "http://localhost:9000")
S3_ACCESS_KEY = _s("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = _s("S3_SECRET_KEY", "change_me_too")
S3_BUCKET = _s("S3_BUCKET", "onboarding-files")

SESSION_TTL = 12 * 60 * 60
CODE_PREFIX = "admin:code:"
SESSION_PREFIX = "admin:sess:"
HR_ROLES = ("hr", "hr_head")


def validate() -> None:
    """Не стартуем в небезопасной конфигурации."""
    if ACCESS not in ("off", "hr", "anyone"):
        raise RuntimeError(f"ADMIN_ACCESS должен быть off|hr|anyone, сейчас: {ACCESS!r}")
    if ACCESS != "off" and len(SESSION_SECRET) < 32:
        raise RuntimeError("SESSION_SECRET: задайте случайную строку от 32 символов (openssl rand -hex 32)")
