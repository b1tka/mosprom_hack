"""Подготовка базы знаний: файлы -> текст (с OCR для сканов) -> чанки -> JSON + Chroma.

Логика взята из ноутбука v002, только оформлена модулем.

    python -m app.ingest upload папка_или_файл   # загрузить документы в S3 (knowledge/)
    python -m app.ingest build --from-s3          # скачать все документы из S3 и пересобрать базу
    python -m app.ingest build [--dir папка]      # пересобрать базу из локальной папки (по умолчанию DATA_DIR)
    python -m app.ingest add путь/к/файлу [--s3]  # добавить/обновить документ (--s3: и загрузить в S3)
    python -m app.ingest delete имя.pdf [--s3]    # удалить документ из базы (--s3: и из S3)
"""
import argparse
import io
import tempfile
import json
import os
import re
from typing import Dict, List

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app import config

SUPPORTED = (".pdf", ".txt", ".md")

_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
    length_function=len,
    separators=["\n\n", "\n", ".", " ", ""],
)

_ocr_reader = None


def _get_ocr_reader():
    """easyocr тяжёлый - грузим только если реально встретился скан."""
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        print("Загрузка OCR-модели...")
        _ocr_reader = easyocr.Reader(["ru", "en"], gpu=False)
    return _ocr_reader


# ---------- текст ----------

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s\.\,\;\:\!\?\-\«\»\(\)\%\+\/\d]", "", text)
    return text.strip()


def extract_text_from_pdf_smart(pdf_path: str) -> str:
    import numpy as np
    import pymupdf as fitz
    from PIL import Image

    full_text = ""
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc):
            page_text = page.get_text()
            if len(page_text.strip()) < config.OCR_MIN_CHARS:
                print(f"  [OCR] Страница {page_num + 1} (скан)...")
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                result = _get_ocr_reader().readtext(np.array(img), detail=0, paragraph=True)
                page_text = "\n".join(result)
            full_text += page_text + "\n"
    return full_text


def extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf_smart(path)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# ---------- чанки ----------

def process_file_to_chunks(path: str) -> List[Dict]:
    file_name = os.path.basename(path)
    print(f"Обработка файла: {file_name}")

    clean_txt = clean_text(extract_text(path))
    if len(clean_txt) < 100:
        print(f"Файл {file_name} пуст или не распознан. Пропускаем.")
        return []

    chunks = _text_splitter.split_text(clean_txt)
    print(f"Получено {len(chunks)} чанков (длина текста: {len(clean_txt)})")
    return [{"text": chunk, "source": file_name} for chunk in chunks]


def build_knowledge_base(folder: str) -> List[Dict]:
    all_data: List[Dict] = []
    for filename in sorted(os.listdir(folder)):
        if filename.lower().endswith(SUPPORTED):
            all_data.extend(process_file_to_chunks(os.path.join(folder, filename)))
    print(f"\nВСЕГО СОБРАНО ЧАНКОВ: {len(all_data)}")
    return all_data


# ---------- JSON ----------

def save_knowledge_base(data: List[Dict], filepath: str = config.KB_PATH) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"База сохранена в {filepath} ({len(data)} чанков)")


def load_knowledge_base(filepath: str = config.KB_PATH) -> List[Dict]:
    if not os.path.exists(filepath):
        print(f"Файл {filepath} не найден. Возвращаю пустую базу.")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"База загружена из {filepath} ({len(data)} чанков)")
    return data


# ---------- операции над базой (JSON + Chroma вместе) ----------

def build_all(folder: str = config.DATA_DIR) -> int:
    from app import vectorstore
    data = build_knowledge_base(folder)
    save_knowledge_base(data)
    if not data:
        return 0
    print("Создаю векторную базу...")
    total = vectorstore.rebuild(data)
    print(f"Векторная база готова. Векторов: {total}")
    return total


def add_document(file_path: str) -> dict:
    from app import vectorstore
    file_name = os.path.basename(file_path)

    new_chunks = process_file_to_chunks(file_path)
    if not new_chunks:
        return {"status": "error", "chunks_added": 0}

    data = [c for c in load_knowledge_base() if c["source"] != file_name]
    data.extend(new_chunks)
    save_knowledge_base(data)

    vectorstore.remove_source(file_name)  # заменяем старую версию, если была
    vectorstore.add_chunks(new_chunks)
    return {"status": "success", "chunks_added": len(new_chunks), "source": file_name}


def delete_document(file_name: str) -> dict:
    from app import vectorstore
    data = load_knowledge_base()
    filtered = [c for c in data if c["source"] != file_name]
    deleted = len(data) - len(filtered)
    if deleted == 0:
        print(f"Файл {file_name} не найден в базе.")
        return {"status": "not_found", "chunks_deleted": 0}

    save_knowledge_base(filtered)
    vectorstore.remove_source(file_name)
    return {"status": "success", "chunks_deleted": deleted, "source": file_name}


def upload(path: str) -> list:
    """Загружает в S3 файл или все поддерживаемые файлы папки."""
    from app import s3_docs
    files = [path] if os.path.isfile(path) else [
        os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(SUPPORTED)
    ]
    keys = [s3_docs.upload_file(f) for f in files]
    for key in keys:
        print(f"Загружено в S3: {key}")
    return keys


def build_from_s3() -> int:
    from app import s3_docs
    with tempfile.TemporaryDirectory() as tmp:
        paths = s3_docs.download_all(tmp)
        print(f"Скачано из S3: {len(paths)} файлов")
        return build_all(tmp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("upload", help="загрузить документы в S3").add_argument("path")
    p_build = sub.add_parser("build", help="пересобрать базу")
    p_build.add_argument("--dir", default=config.DATA_DIR)
    p_build.add_argument("--from-s3", action="store_true", help="взять документы из S3")
    p_add = sub.add_parser("add", help="добавить документ")
    p_add.add_argument("path")
    p_add.add_argument("--s3", action="store_true", help="также загрузить в S3")
    p_del = sub.add_parser("delete", help="удалить документ")
    p_del.add_argument("name")
    p_del.add_argument("--s3", action="store_true", help="также удалить из S3")
    args = parser.parse_args()

    if args.cmd == "upload":
        upload(args.path)
    elif args.cmd == "build":
        build_from_s3() if args.from_s3 else build_all(args.dir)
    elif args.cmd == "add":
        result = add_document(args.path)
        if args.s3 and result["status"] == "success":
            upload(args.path)
        print(result)
    elif args.cmd == "delete":
        result = delete_document(args.name)
        if args.s3:
            from app import s3_docs
            s3_docs.delete_document(args.name)
        print(result)


if __name__ == "__main__":
    main()
