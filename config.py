"""Все настройки проекта. Любую можно переопределить переменной окружения."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- LLM (локальная meno-lite-0.1, OpenAI-совместимый сервер) ---
MENO_BASE_URL = os.getenv("MENO_BASE_URL", "http://localhost:11434/v1")
MENO_MODEL = os.getenv("MENO_MODEL", "hf.co/bond005/meno-lite-0.1-gguf:Q4_K_M")
MENO_API_KEY = os.getenv("MENO_API_KEY", "EMPTY")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

# --- Данные и векторная база ---
DATA_DIR = os.getenv("DATA_DIR", str(BASE_DIR / "data"))              # папка с исходными файлами
KB_PATH = os.getenv("KB_PATH", str(BASE_DIR / "knowledge_base.json"))  # чанки в JSON
CHROMA_DIR = os.getenv("CHROMA_DIR", str(BASE_DIR / "chroma_db"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-large")
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))

# --- Разбиение на чанки ---
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
OCR_MIN_CHARS = int(os.getenv("OCR_MIN_CHARS", "50"))  # меньше символов на странице -> считаем сканом

# --- Хранилище статистики: "file" (для тестов, без Redis) или "redis" ---
STORE_BACKEND = os.getenv("STORE_BACKEND", "file")
STATS_FILE = os.getenv("STATS_FILE", str(BASE_DIR / "stats_store.json"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "onboarding:stats:")

# --- Kafka ---
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "onboarding.answers")
KAFKA_GROUP = os.getenv("KAFKA_GROUP", "corsik-mood")
