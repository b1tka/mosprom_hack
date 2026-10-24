"""Эмбеддинги и векторная база Chroma (общие для индексации и для RAG)."""
from functools import lru_cache
from typing import Dict, List

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from app import config

_BATCH = 500


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=config.EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache(maxsize=1)
def get_vector_store() -> Chroma:
    return Chroma(persist_directory=config.CHROMA_DIR, embedding_function=get_embeddings())


def _to_docs(chunks: List[Dict]) -> List[Document]:
    return [Document(page_content=c["text"], metadata={"source": c["source"]}) for c in chunks]


def add_chunks(chunks: List[Dict]) -> None:
    docs = _to_docs(chunks)
    store = get_vector_store()
    for i in range(0, len(docs), _BATCH):
        store.add_documents(docs[i:i + _BATCH])


def remove_source(file_name: str) -> None:
    """Удаляет из базы все чанки одного файла."""
    get_vector_store()._collection.delete(where={"source": file_name})


def rebuild(chunks: List[Dict]) -> int:
    """Полностью пересоздаёт коллекцию (повторный запуск не плодит дубликаты)."""
    get_vector_store().delete_collection()
    get_vector_store.cache_clear()
    add_chunks(chunks)
    return count()


def count() -> int:
    return get_vector_store()._collection.count()
