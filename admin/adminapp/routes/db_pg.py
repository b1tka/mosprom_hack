import json
import re

import psycopg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from psycopg import sql

from adminapp import auth
from adminapp.clients import pg_pool
from adminapp.dbcommon import CONFIRM_WORD, PAGE, fmt_value, full_value, pages, require_confirm
from adminapp.ui import render

router = APIRouter(prefix="/db/pg")

READ_WORDS = {"SELECT", "WITH", "SHOW", "EXPLAIN", "VALUES", "TABLE"}
MAX_ROWS = 500
STATEMENT_TIMEOUT_MS = 30_000


def _q(sql_text: str, params=None, *, read_only: bool = True):
    with pg_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text, params)
            return [d.name for d in cur.description] if cur.description else [], cur.fetchall() if cur.description else []


def _tables() -> list[str]:
    cols, rows = _q("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name")
    return [list(r.values())[0] for r in rows]


def _table_or_404(name: str) -> str:
    if name not in _tables():
        raise HTTPException(404, "Таблица не найдена")
    return name


def _columns(table: str) -> list[dict]:
    _, rows = _q("SELECT column_name AS name, data_type AS type, is_nullable = 'YES' AS nullable FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position", (table,))
    return rows


def _pk(table: str) -> list[str]:
    _, rows = _q(
        """SELECT a.attname AS name FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = %s::regclass AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum)""",
        (f'public."{table}"',),
    )
    return [r["name"] for r in rows]


@router.get("", response_class=HTMLResponse)
def tables(request: Request, s: auth.Session = Depends(auth.require_user)):
    items = []
    for t in _tables():
        _, r = _q(sql.SQL("SELECT count(*)::int AS n FROM {}").format(sql.Identifier(t)).as_string())
        items.append({"name": t, "rows": r[0]["n"]})
    return render(request, "pg_tables.html", {"items": items})


@router.get("/table/{table}", response_class=HTMLResponse)
def table_view(request: Request, table: str, page: int = 1, q: str = "", s: auth.Session = Depends(auth.require_user)):
    _table_or_404(table)
    cols, pk = _columns(table), _pk(table)
    ident = sql.Identifier(table)
    where, params = sql.SQL(""), []
    if q.strip():
        where = sql.SQL(" WHERE ") + sql.SQL(" OR ").join(sql.SQL("{}::text ILIKE %s").format(sql.Identifier(c["name"])) for c in cols)
        params = [f"%{q.strip()}%"] * len(cols)
    with pg_pool().connection() as conn:
        total = conn.execute(sql.SQL("SELECT count(*)::int AS n FROM {}").format(ident) + where, params).fetchone()["n"]
        pg = pages(total, page)
        order = sql.SQL(" ORDER BY ") + sql.SQL(", ").join(sql.Identifier(c) for c in pk) if pk else sql.SQL("")
        rows = conn.execute(sql.SQL("SELECT * FROM {}").format(ident) + where + order + sql.SQL(" LIMIT %s OFFSET %s"), params + [PAGE, pg["offset"]]).fetchall()
    out = [{"cells": [fmt_value(r[c["name"]]) for c in cols], "pk": json.dumps({k: str(r[k]) for k in pk}, ensure_ascii=False) if pk else None} for r in rows]
    return render(request, "pg_table.html", {"table": table, "cols": cols, "pk": pk, "rows": out, "q": q, "pg": pg})


def _row(table: str, pk_json: str):
    pk = _pk(table)
    if not pk:
        raise HTTPException(400, "У таблицы нет первичного ключа: правка недоступна")
    try:
        key = json.loads(pk_json)
    except ValueError:
        raise HTTPException(400, "Неверный ключ строки")
    if set(key) != set(pk):
        raise HTTPException(400, "Ключ строки не соответствует таблице")
    cond = sql.SQL(" AND ").join(sql.SQL("{}::text = %s").format(sql.Identifier(c)) for c in pk)
    return pk, key, cond


@router.get("/table/{table}/row", response_class=HTMLResponse)
def row_form(request: Request, table: str, pk: str, s: auth.Session = Depends(auth.require_user)):
    _table_or_404(table)
    pk_cols, key, cond = _row(table, pk)
    with pg_pool().connection() as conn:
        row = conn.execute(sql.SQL("SELECT * FROM {} WHERE ").format(sql.Identifier(table)) + cond, [str(key[c]) for c in pk_cols]).fetchone()
    if not row:
        raise HTTPException(404, "Строка не найдена")
    cols = [{**c, "value": full_value(row[c["name"]]), "is_null": row[c["name"]] is None, "is_pk": c["name"] in pk_cols} for c in _columns(table)]
    return render(request, "pg_row.html", {"table": table, "cols": cols, "pk_json": pk, "message": "", "CONFIRM_WORD": CONFIRM_WORD})


@router.post("/table/{table}/row", response_class=HTMLResponse)
async def row_update(request: Request, table: str, s: auth.Session = Depends(auth.require_csrf)):
    _table_or_404(table)
    form = await request.form()
    require_confirm(str(form.get("confirm", "")))
    pk_json = str(form.get("pk", ""))
    pk_cols, key, cond = _row(table, pk_json)
    where_params = [str(key[c]) for c in pk_cols]
    with pg_pool().connection() as conn:
        cur = conn.execute(sql.SQL("SELECT * FROM {} WHERE ").format(sql.Identifier(table)) + cond, where_params).fetchone()
        if not cur:
            raise HTTPException(404, "Строка не найдена")
        sets, params, changed = [], [], {}
        for c in _columns(table):
            n = c["name"]
            if n in pk_cols or (f"c__{n}" not in form and f"n__{n}" not in form):
                continue   # колонки, которых нет в форме, не трогаем
            new_null = form.get(f"n__{n}") == "on"
            new_val = str(form.get(f"c__{n}", ""))
            old_null, old_val = cur[n] is None, full_value(cur[n])
            if new_null and not old_null:
                if not c["nullable"]:
                    raise HTTPException(400, f"Колонка {n} не допускает NULL")
                sets.append(sql.SQL("{} = NULL").format(sql.Identifier(n)))
                changed[n] = None
            elif not new_null and (old_null or new_val != old_val):
                sets.append(sql.SQL("{} = %s").format(sql.Identifier(n)))
                params.append(new_val)
                changed[n] = new_val[:200]
        if not sets:
            msg = "Изменений нет."
        else:
            try:
                conn.execute(sql.SQL("UPDATE {} SET ").format(sql.Identifier(table)) + sql.SQL(", ").join(sets) + sql.SQL(" WHERE ") + cond, params + where_params)
            except psycopg.Error as exc:
                raise HTTPException(400, f"Ошибка БД: {str(exc).splitlines()[0]}")
            msg = f"Сохранено: {', '.join(changed)}."
    auth.audit(s, "pg.update_row", table=table, pk=key, changed=changed)
    return await _row_response(request, table, pk_json, msg)


async def _row_response(request, table, pk_json, message):
    pk_cols, key, cond = _row(table, pk_json)
    with pg_pool().connection() as conn:
        row = conn.execute(sql.SQL("SELECT * FROM {} WHERE ").format(sql.Identifier(table)) + cond, [str(key[c]) for c in pk_cols]).fetchone()
    cols = [{**c, "value": full_value(row[c["name"]]), "is_null": row[c["name"]] is None, "is_pk": c["name"] in pk_cols} for c in _columns(table)]
    return render(request, "_pg_row_form.html", {"table": table, "cols": cols, "pk_json": pk_json, "message": message, "CONFIRM_WORD": CONFIRM_WORD})


@router.post("/table/{table}/delete", response_class=HTMLResponse)
def row_delete(request: Request, table: str, pk: str = Form(""), confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    _table_or_404(table)
    require_confirm(confirm)
    pk_cols, key, cond = _row(table, pk)
    try:
        with pg_pool().connection() as conn:
            cur = conn.execute(sql.SQL("DELETE FROM {} WHERE ").format(sql.Identifier(table)) + cond, [str(key[c]) for c in pk_cols])
            n = cur.rowcount
    except psycopg.Error as exc:
        raise HTTPException(400, f"Ошибка БД: {str(exc).splitlines()[0]}")
    auth.audit(s, "pg.delete_row", table=table, pk=key, deleted=n)
    return HTMLResponse(f'<p class="ok">Удалено строк: {n}. <a href="/db/pg/table/{table}">К таблице</a></p>')


# ---------- SQL-консоль ----------

def _first_word(text: str) -> str:
    t = re.sub(r"(--[^\n]*)|(/\*.*?\*/)", " ", text, flags=re.S).strip().lstrip("(")
    m = re.match(r"[A-Za-z]+", t)
    return m.group(0).upper() if m else ""


@router.get("/sql", response_class=HTMLResponse)
def sql_form(request: Request, s: auth.Session = Depends(auth.require_user)):
    return render(request, "pg_sql.html", {"result": None, "query": "", "CONFIRM_WORD": CONFIRM_WORD, "error": ""})


@router.post("/sql", response_class=HTMLResponse)
def sql_run(request: Request, query: str = Form(""), confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    query = query.strip().rstrip(";").strip()
    ctx = {"query": query, "CONFIRM_WORD": CONFIRM_WORD, "result": None, "error": ""}
    if not query:
        return render(request, "_pg_sql_result.html", {**ctx, "error": "Введите запрос."})
    word = _first_word(query)
    readonly = word in READ_WORDS
    if not readonly and (confirm or "").strip() != CONFIRM_WORD:
        return render(request, "_pg_sql_result.html", {**ctx, "error": f"Запрос изменяет данные или схему ({word or '?'}). Введите слово «{CONFIRM_WORD}» для подтверждения."})
    try:
        with pg_pool().connection() as conn:
            conn.autocommit = False
            try:
                conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                if readonly:
                    conn.execute("SET TRANSACTION READ ONLY")   # PG сам откажет, если «чтение» пытается писать
                cur = conn.execute(query)
                if cur.description:
                    cols = [d.name for d in cur.description]
                    rows = cur.fetchmany(MAX_ROWS + 1)
                    result = {"cols": cols, "rows": [[fmt_value(r[c], 500) for c in cols] for r in rows[:MAX_ROWS]], "truncated": len(rows) > MAX_ROWS}
                else:
                    result = {"cols": [], "rows": [], "affected": cur.rowcount, "truncated": False}
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = True
    except psycopg.Error as exc:
        auth.audit(s, "pg.sql", sql=query[:2000], readonly=readonly, error=str(exc).splitlines()[0][:200])
        return render(request, "_pg_sql_result.html", {**ctx, "error": f"Ошибка БД: {str(exc).splitlines()[0]}"})
    auth.audit(s, "pg.sql", sql=query[:2000], readonly=readonly, rows=len(result["rows"]), affected=result.get("affected"))
    return render(request, "_pg_sql_result.html", {**ctx, "result": result})
