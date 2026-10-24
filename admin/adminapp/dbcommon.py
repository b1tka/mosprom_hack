"""Общие помощники раздела «Базы данных»: подтверждение опасных операций, постраничность, форматирование значений."""
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import HTTPException

CONFIRM_WORD = "ПОДТВЕРЖДАЮ"
PAGE = 50


def require_confirm(confirm: str) -> None:
    """Опасные операции (изменение, удаление, DROP) выполняются только с введённым словом-подтверждением."""
    if (confirm or "").strip() != CONFIRM_WORD:
        raise HTTPException(400, f"Для этой операции введите слово «{CONFIRM_WORD}»")


def fmt_value(v: Any, limit: int = 300) -> str:
    """Значение ячейки для таблицы: NULL, bytes, JSON, даты."""
    if v is None:
        return "NULL"
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return f"\\x{b[:24].hex()}{'…' if len(b) > 24 else ''} ({len(b)} байт)"
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    elif isinstance(v, (datetime, date, time, Decimal)):
        s = str(v)
    else:
        s = str(v)
    return s if len(s) <= limit else s[:limit] + "…"


def full_value(v: Any) -> str:
    """Полное строковое значение для редактора (без обрезки)."""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(v).hex()
    return str(v)


def pages(total: int, page: int, size: int = PAGE) -> dict:
    n = max(1, -(-total // size))
    page = min(max(1, page), n)
    return {"page": page, "pages": n, "offset": (page - 1) * size, "total": total}
