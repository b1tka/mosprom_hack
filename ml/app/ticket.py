"""Короткая тема и категория обращения сотрудника к HR (для списка тикетов у HR)."""
import json
import logging
import re
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import get_llm
from app.prompts import REASONS, TICKET_TITLE_PROMPT

log = logging.getLogger(__name__)

MAX_TITLE_LEN = 80


def _parse(raw: str) -> Optional[dict]:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    title = str(data.get("title") or "").strip().strip("«»\"'").rstrip(".").strip()
    category = data.get("category")
    if not title or category not in REASONS:
        return None
    return {"title": title[:MAX_TITLE_LEN], "category": category}


def make_title(text: str, retries: int = 1) -> Optional[dict]:
    """Возвращает {"title", "category"} или None, если ответ модели разобрать не удалось."""
    messages = [SystemMessage(content=TICKET_TITLE_PROMPT), HumanMessage(content=text)]
    for attempt in range(retries + 1):
        raw = get_llm().invoke(messages).content or ""
        parsed = _parse(raw)
        if parsed:
            return parsed
        log.warning("Не удалось разобрать тему обращения (попытка %s): %r", attempt + 1, raw[:200])
    return None
