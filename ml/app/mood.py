"""Классификация сообщений сотрудников (mood / reason / urgent) и обновление статистики."""
import copy
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import get_llm
from app.prompts import DIALOG_INTRO, FORMAT_SUFFIX, MOODS, REASONS, SYSTEM_PROMPT
from app.store import get_store

log = logging.getLogger(__name__)


# ---------- классификация ----------

def _parse(raw: str) -> Optional[dict]:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    mood, reason = data.get("mood"), data.get("reason")
    if mood not in MOODS or reason not in REASONS:
        return None

    urgent = data.get("urgent")
    if isinstance(urgent, str):
        urgent = urgent.strip().lower() == "true"
    urgent = bool(urgent)

    explanation = (data.get("explanation") or "").strip() if urgent else ""  # правило 4 промпта
    return {"mood": mood, "reason": reason, "urgent": urgent, "explanation": explanation}


def classify_message(text: str, retries: int = 1, system: str = SYSTEM_PROMPT) -> Optional[dict]:
    """Возвращает {"mood", "reason", "urgent", "explanation"} или None, если разобрать не удалось."""
    messages = [SystemMessage(content=system + FORMAT_SUFFIX), HumanMessage(content=text)]
    for attempt in range(retries + 1):
        raw = get_llm().invoke(messages).content or ""
        parsed = _parse(raw)
        if parsed:
            return parsed
        log.warning("Не удалось разобрать ответ классификатора (попытка %s): %r", attempt + 1, raw[:200])
    return None


def classify_dialog(transcript: str, retries: int = 1) -> Optional[dict]:
    """Один вердикт на весь диалог с HR: настроение сотрудника по переписке в целом."""
    return classify_message(transcript, retries, system=DIALOG_INTRO + "\n\n" + SYSTEM_PROMPT)


# ---------- статистика ----------

def empty_stats(stage: Optional[str] = None) -> dict:
    return {
        "stage": stage,
        "total_requests": 0,
        "moods": {m: 0 for m in MOODS},
        "reasons": {r: 0 for r in REASONS},
        "red_flag": False,
        "last_reason": "",
    }


def update_stats(old_stats: Optional[dict], classification: dict, stage: Optional[str] = None) -> dict:
    """Чистая функция: берёт старую статистику, возвращает НОВУЮ (старая не мутируется)."""
    new = copy.deepcopy(old_stats) if old_stats else empty_stats(stage)

    # на случай старых/неполных записей в хранилище
    for key, default in empty_stats(stage).items():
        new.setdefault(key, default)
    for m in MOODS:
        new["moods"].setdefault(m, 0)
    for r in REASONS:
        new["reasons"].setdefault(r, 0)

    new["total_requests"] += 1
    new["moods"][classification["mood"]] += 1
    new["reasons"][classification["reason"]] += 1
    if stage:
        new["stage"] = stage

    if classification["urgent"]:
        new["red_flag"] = True  # сбрасывать флаг должен HR, а не следующее "нормальное" сообщение
        new["last_reason"] = classification["explanation"]
    return new


# ---------- хранилище ----------

def get_stats(uid: str) -> Optional[dict]:
    return get_store().get(uid)


def _apply(uid: str, classification: dict, stage: Optional[str], old_stats: Optional[dict] = None,
           source: str = "bot_question", event_key: Optional[str] = None, ticket_number: Optional[int] = None) -> dict:
    """Под блокировкой: пишет событие в историю, берёт старую статистику, обновляет, записывает в хранилище.

    Событие с уже виденным event_key (повторная доставка из Kafka) счётчики не меняет.
    """
    store = get_store()
    with store.lock(uid):
        now = datetime.now(timezone.utc)
        event = {
            "uid": uid, "ts": now, "mood": classification["mood"], "reason": classification["reason"],
            "urgent": classification["urgent"], "source": source, "event_key": event_key,
        }
        if ticket_number is not None:
            event["ticket_number"] = ticket_number
        base = old_stats if old_stats is not None else store.get(uid)
        if not store.add_event(event):
            log.info("uid=%s: событие %s уже учтено, пропускаю", uid, event_key)
            return base or empty_stats(stage)
        new = update_stats(base, classification, stage)
        new["updated_at"] = now.isoformat()
        store.save(uid, new)
    return new


def update_user_stats(uid: str, text: str, old_stats: Optional[dict] = None, stage: Optional[str] = None) -> None:
    """ГЛАВНАЯ ФУНКЦИЯ для интеграции (API и Kafka): расставляет метки и пишет статистику в Redis.

    Ничего не возвращает. Старая статистика:
      - передана в old_stats -> берётся она;
      - не передана (None)   -> читается из хранилища по uid; если пользователя нет - создаётся новая.
    Если модель не смогла классифицировать сообщение, в хранилище ничего не пишется (только лог).
    """
    classification = classify_message(text)  # долгий вызов LLM - вне блокировки
    if classification is None:
        log.warning("uid=%s: классификация не удалась, статистика не изменена", uid)
        return
    _apply(uid, classification, stage, old_stats)


def process_message(uid: str, text: str, stage: Optional[str] = None) -> Optional[dict]:
    """Версия для демо и отладки: то же самое, но возвращает {uid: stats} (или None при неудаче)."""
    classification = classify_message(text)
    if classification is None:
        return None
    return {uid: _apply(uid, classification, stage)}


def process_dialog(uid: str, transcript: str, stage: Optional[str], ticket_number: int, event_key: str) -> Optional[dict]:
    """Диалог закрытого тикета -> один вердикт -> статистика (+1 на тикет). None, если классификация не удалась."""
    classification = classify_dialog(transcript)  # долгий вызов LLM - вне блокировки
    if classification is None:
        return None
    return {uid: _apply(uid, classification, stage, source="ticket", event_key=event_key, ticket_number=ticket_number)}
