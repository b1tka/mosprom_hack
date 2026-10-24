"""Исходные документы базы знаний в S3 (бакет S3_BUCKET, префикс S3_KNOWLEDGE_PREFIX)."""
import os
from functools import lru_cache
from typing import List

import boto3

from app import config


@lru_cache(maxsize=1)
def _client():
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        region_name="us-east-1",
    )


def list_documents() -> List[str]:
    """Ключи всех документов под префиксом базы знаний."""
    keys: List[str] = []
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.S3_BUCKET, Prefix=config.S3_KNOWLEDGE_PREFIX):
        keys.extend(obj["Key"] for obj in page.get("Contents", []) if not obj["Key"].endswith("/"))
    return keys


def download_all(target_dir: str) -> List[str]:
    """Скачивает все документы в target_dir (плоско, по имени файла). Возвращает локальные пути."""
    os.makedirs(target_dir, exist_ok=True)
    paths = []
    for key in list_documents():
        path = os.path.join(target_dir, os.path.basename(key))
        _client().download_file(config.S3_BUCKET, key, path)
        paths.append(path)
    return paths


def upload_file(path: str) -> str:
    key = config.S3_KNOWLEDGE_PREFIX + os.path.basename(path)
    _client().upload_file(path, config.S3_BUCKET, key)
    return key


def delete_document(file_name: str) -> None:
    _client().delete_object(Bucket=config.S3_BUCKET, Key=config.S3_KNOWLEDGE_PREFIX + file_name)


# ---------- входящие файлы от бота (S3_INCOMING_PREFIX/<id>/<имя>) ----------

def exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=config.S3_BUCKET, Key=key)
        return True
    except _client().exceptions.ClientError:
        return False


def download(key: str, target_dir: str) -> str:
    path = os.path.join(target_dir, os.path.basename(key))
    _client().download_file(config.S3_BUCKET, key, path)
    return path


def promote(key: str) -> str:
    """Переносит входящий файл в базу знаний (knowledge/<имя>), заменяя одноимённый. Возвращает новый ключ."""
    target = config.S3_KNOWLEDGE_PREFIX + os.path.basename(key)
    _client().copy_object(Bucket=config.S3_BUCKET, Key=target, CopySource={"Bucket": config.S3_BUCKET, "Key": key})
    delete_key(key)
    return target


def delete_key(key: str) -> None:
    _client().delete_object(Bucket=config.S3_BUCKET, Key=key)
