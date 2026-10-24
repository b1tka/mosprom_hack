"""Данные нового сотрудника из PDF: PDF -> текст (OCR для сканов) -> LLM -> проверенные поля.

Модуль из ветки pdf_user_load (pdf_user_pars.py, users_router.py), переписан под сервис: общий LLM-клиент (app.llm),
текст из ingest (с OCR), поля тег и руководитель, защита от «выдуманных» LLM значений. Один PDF — один человек;
если в файле несколько человек, возвращаются все найденные, бот берёт первого.
"""
import json
import logging
import os
import re
import tempfile
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import get_llm

log = logging.getLogger(__name__)

MAX_TEXT = 6000          # символов в LLM: анкеты короткие, длинные документы обрезаем
TAGS = ("GTP", "GrPP", "Luzhniki")
# как тег может быть написан в документе -> каноническое имя (как в таблице tag)
TAG_ALIASES = {
    "gtp": "GTP", "гтп": "GTP",
    "grpp": "GrPP", "грпп": "GrPP",
    "luzhniki": "Luzhniki", "лужники": "Luzhniki",
}

SYSTEM_PROMPT = """Ты — HR-ассистент. По тексту анкеты, карточки или приказа о приёме извлеки данные НОВОГО сотрудника.
Верни ТОЛЬКО JSON-массив, без markdown и пояснений. Формат одного элемента:
[{"name": "Имя", "surname": "Фамилия", "email": "почта", "phone": "телефон", "tag": "GTP|GrPP|Luzhniki", "director": "ФИО руководителя"}]
Правила:
1. Имя и фамилию пиши так, как они написаны в тексте (отчество не нужно).
2. email и phone копируй из текста как есть. Не выдумывай: если данных нет, ставь null.
3. tag — площадка или подразделение: GTP (также ГТП), GrPP (ГрПП), Luzhniki (Лужники). Если в тексте они не упомянуты, ставь null.
4. director — ФИО непосредственного руководителя из текста (как написано), иначе null.
5. Если в тексте несколько новых сотрудников, верни по одному элементу на каждого."""

_EMAIL = re.compile(r"[^\s@<>()\[\],;:]+@[^\s@<>()\[\],;:]+\.[^\s@<>()\[\],;:]+")


def extract_text(pdf_bytes: bytes) -> str:
    """Текст PDF; для страниц-сканов включается OCR (тот же код, что у базы знаний)."""
    from app import ingest
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "in.pdf")
        with open(path, "wb") as f:
            f.write(pdf_bytes)
        return ingest.extract_text_from_pdf_smart(path).strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def normalize_tag(value: Optional[str]) -> Optional[str]:
    return TAG_ALIASES.get(_norm(value or "")) if value else None


def _parse(raw: str) -> list[dict]:
    """Достаёт JSON-массив (или одиночный объект) из ответа модели: она может добавить markdown и пояснения."""
    start, end = raw.find("["), raw.rfind("]")
    candidates = []
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])
    s2, e2 = raw.find("{"), raw.rfind("}")
    if s2 != -1 and e2 > s2:
        candidates.append("[" + raw[s2:e2 + 1] + "]")
    for text in candidates:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        return [d for d in data if isinstance(d, dict)]
    return []


def verify(person: dict, text: str) -> Optional[dict]:
    """Оставляет только то, что подтверждается текстом PDF. None — человек выдуман (имени или фамилии нет в тексте)."""
    low, text_digits = _norm(text), _digits(text)
    name, surname = (person.get("name") or "").strip(), (person.get("surname") or "").strip()
    # словоформы допускаем (Иванова/Иванов): сравниваем по основе без последней буквы
    if not name or not surname or _norm(name)[:-1] not in low or _norm(surname)[:-1] not in low:
        return None
    email = (person.get("email") or "").strip()
    email = email if email and _EMAIL.fullmatch(email) and email.lower() in text.lower() else None
    phone = (person.get("phone") or "").strip()
    pd = _digits(phone)
    phone = phone if len(pd) >= 10 and pd[-10:] in text_digits else None
    director = (person.get("director") or "").strip()
    # руководитель подтверждён, если в тексте есть его фамилия (первое слово); имя в словоформах и совпадения с чужой фамилией не считаем
    words = _norm(director).split()
    director = director if words and len(words[0]) > 3 and words[0][:-1] in low else None
    return {"name": name, "surname": surname, "email": email, "phone": phone,
            "tag": normalize_tag(person.get("tag")), "director": director}


def extract_users(pdf_bytes: bytes) -> list[dict]:
    """-> список найденных людей (обычно один). ValueError — в PDF нет текста; исключения LLM пробрасываются (-> 503)."""
    text = extract_text(pdf_bytes)
    if not text:
        raise ValueError("no text in pdf")
    return users_from_text(text)


def users_from_text(text: str) -> list[dict]:
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=f"Текст:\n{text[:MAX_TEXT]}")]
    for attempt in range(2):
        raw = get_llm().invoke(messages).content or ""
        people = _parse(raw)
        if people or attempt == 1:
            break
        log.warning("Не удалось разобрать ответ при извлечении сотрудника: %r", raw[:200])
    result = [v for v in (verify(p, text) for p in people) if v]
    return result
