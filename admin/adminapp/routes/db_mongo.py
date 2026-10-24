import json
from urllib.parse import quote, unquote

from bson import json_util
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pymongo.errors import PyMongoError

from adminapp import auth
from adminapp.clients import mdb
from adminapp.dbcommon import CONFIRM_WORD, PAGE, pages, require_confirm
from adminapp.ui import render

router = APIRouter(prefix="/db/mongo")

FORBIDDEN_OPS = {"$where", "$function", "$accumulator"}    # выполнение JS на сервере запрещаем
JSON_OPTS = json_util.RELAXED_JSON_OPTIONS


def _coll_or_404(name: str) -> str:
    if name not in mdb().list_collection_names():
        raise HTTPException(404, "Коллекция не найдена")
    return name


def _dumps(obj, indent=None) -> str:
    return json_util.dumps(obj, json_options=JSON_OPTS, ensure_ascii=False, indent=indent)


def _loads(text: str):
    try:
        return json_util.loads(text, json_options=JSON_OPTS)
    except Exception:
        raise HTTPException(400, "Неверный JSON")


def _check_ops(node) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in FORBIDDEN_OPS:
                raise HTTPException(400, f"Оператор {k} запрещён")
            _check_ops(v)
    elif isinstance(node, list):
        for v in node:
            _check_ops(v)


def _id_str(_id) -> str:
    return quote(_dumps(_id), safe="")


def _id_parse(raw: str):
    return _loads(unquote(raw))


@router.get("", response_class=HTMLResponse)
def collections(request: Request, s: auth.Session = Depends(auth.require_user)):
    db = mdb()
    items = [{"name": n, "count": db[n].estimated_document_count()} for n in sorted(db.list_collection_names())]
    return render(request, "mongo_colls.html", {"items": items})


@router.get("/c/{coll}", response_class=HTMLResponse)
def collection(request: Request, coll: str, page: int = 1, filter: str = "", s: auth.Session = Depends(auth.require_user)):
    _coll_or_404(coll)
    flt = _loads(filter) if filter.strip() else {}
    if not isinstance(flt, dict):
        raise HTTPException(400, "Фильтр должен быть JSON-объектом")
    _check_ops(flt)
    col = mdb()[coll]
    try:
        total = col.count_documents(flt, maxTimeMS=10_000)
        pg = pages(total, page)
        docs = list(col.find(flt).sort("_id", -1).skip(pg["offset"]).limit(PAGE).max_time_ms(10_000))
    except PyMongoError as exc:
        raise HTTPException(400, f"Ошибка Mongo: {str(exc)[:200]}")
    items = [{"id": _id_str(d["_id"]), "json": _dumps(d, indent=2)[:6000]} for d in docs]
    return render(request, "mongo_coll.html", {"coll": coll, "items": items, "pg": pg, "filter": filter, "CONFIRM_WORD": CONFIRM_WORD})


@router.get("/c/{coll}/doc", response_class=HTMLResponse)
def doc_form(request: Request, coll: str, id: str, s: auth.Session = Depends(auth.require_user)):
    _coll_or_404(coll)
    doc = mdb()[coll].find_one({"_id": _id_parse(id)})
    if doc is None:
        raise HTTPException(404, "Документ не найден")
    return render(request, "mongo_doc.html", {"coll": coll, "id": id, "json": _dumps(doc, indent=2), "message": "", "error": "", "CONFIRM_WORD": CONFIRM_WORD})


def _doc_view(request, coll, id, json_text, message="", error=""):
    return render(request, "_mongo_doc_form.html", {"coll": coll, "id": id, "json": json_text, "message": message, "error": error, "CONFIRM_WORD": CONFIRM_WORD})


@router.post("/c/{coll}/doc", response_class=HTMLResponse)
def doc_save(request: Request, coll: str, id: str = Form(""), body: str = Form(""), confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    _coll_or_404(coll)
    require_confirm(confirm)
    oid = _id_parse(id)
    new = _loads(body)
    if not isinstance(new, dict):
        return _doc_view(request, coll, id, body, error="Документ должен быть JSON-объектом.")
    if "_id" in new and new["_id"] != oid:
        return _doc_view(request, coll, id, body, error="_id менять нельзя.")
    _check_ops(new)
    new["_id"] = oid
    try:
        res = mdb()[coll].replace_one({"_id": oid}, new)
    except PyMongoError as exc:
        return _doc_view(request, coll, id, body, error=f"Ошибка Mongo: {str(exc)[:200]}")
    auth.audit(s, "mongo.replace_doc", collection=coll, id=_dumps(oid), matched=res.matched_count, size=len(body))
    return _doc_view(request, coll, id, _dumps(new, indent=2), message="Документ сохранён." if res.matched_count else "Документ не найден (удалён?).")


@router.post("/c/{coll}/delete", response_class=HTMLResponse)
def doc_delete(request: Request, coll: str, id: str = Form(""), confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    _coll_or_404(coll)
    require_confirm(confirm)
    oid = _id_parse(id)
    n = mdb()[coll].delete_one({"_id": oid}).deleted_count
    auth.audit(s, "mongo.delete_doc", collection=coll, id=_dumps(oid), deleted=n)
    return HTMLResponse(f'<p class="ok">Удалено документов: {n}. <a href="/db/mongo/c/{coll}">К коллекции</a></p>')


@router.post("/c/{coll}/insert", response_class=HTMLResponse)
def doc_insert(request: Request, coll: str, body: str = Form(""), confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    _coll_or_404(coll)
    require_confirm(confirm)
    doc = _loads(body)
    if not isinstance(doc, dict):
        raise HTTPException(400, "Документ должен быть JSON-объектом")
    _check_ops(doc)
    try:
        res = mdb()[coll].insert_one(doc)
    except PyMongoError as exc:
        raise HTTPException(400, f"Ошибка Mongo: {str(exc)[:200]}")
    auth.audit(s, "mongo.insert_doc", collection=coll, id=_dumps(res.inserted_id))
    return HTMLResponse(f'<p class="ok">Документ добавлен. <a href="/db/mongo/c/{coll}">К коллекции</a></p>')


@router.post("/c/{coll}/drop", response_class=HTMLResponse)
def drop(request: Request, coll: str, confirm: str = Form(""), name: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    _coll_or_404(coll)
    require_confirm(confirm)
    if name != coll:   # удаление коллекции дополнительно требует ввести её имя
        raise HTTPException(400, "Введите имя коллекции для подтверждения удаления")
    n = mdb()[coll].estimated_document_count()
    mdb().drop_collection(coll)
    auth.audit(s, "mongo.drop_collection", collection=coll, documents=n)
    return HTMLResponse(f'<p class="ok">Коллекция «{coll}» удалена (документов: {n}). <a href="/db/mongo">К коллекциям</a></p>')
