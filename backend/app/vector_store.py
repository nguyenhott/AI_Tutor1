from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import chromadb

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CHROMA_DIR = Path(os.getenv("CHROMA_DB_DIR", str(DATA_DIR / "chroma")))
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "course_materials")


def _client():
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection():
    return _client().get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _metadata(chunk: dict, embedding_model: str | None) -> dict[str, Any]:
    return {
        "documentId": str(chunk.get("documentId") or ""),
        "title": str(chunk.get("title") or "Course Document"),
        "sourcePath": str(chunk.get("sourcePath") or ""),
        "page": int(chunk.get("page") or 0),
        "embeddingModel": str(embedding_model or ""),
    }


def replace_document(document_id: str, chunks: list[dict], embedding_model: str | None) -> dict:
    collection = get_collection()
    try:
        collection.delete(where={"documentId": document_id})
    except Exception:
        # Chroma raises when nothing matches in some versions; replacement can continue.
        pass

    embedded_chunks = [chunk for chunk in chunks if chunk.get("embedding")]
    if embedded_chunks:
        collection.add(
            ids=[str(chunk["chunkId"]) for chunk in embedded_chunks],
            documents=[str(chunk.get("content") or "") for chunk in embedded_chunks],
            embeddings=[chunk["embedding"] for chunk in embedded_chunks],
            metadatas=[_metadata(chunk, embedding_model) for chunk in embedded_chunks],
        )

    return {
        "path": str(CHROMA_DIR),
        "collection": COLLECTION_NAME,
        "writtenVectors": len(embedded_chunks),
        "totalVectors": collection.count(),
    }


def delete_document(document_id: str) -> dict:
    collection = get_collection()
    before = collection.count()
    try:
        collection.delete(where={"documentId": document_id})
    except Exception:
        # Chroma can raise when no records match; deletion should still be idempotent.
        pass
    after = collection.count()
    return {
        "path": str(CHROMA_DIR),
        "collection": COLLECTION_NAME,
        "deletedVectors": max(0, before - after),
        "totalVectors": after,
    }


def query_chunks(query_embedding: list[float], top_k: int = 4, document_id: str | None = None) -> list[dict]:
    collection = get_collection()
    if collection.count() == 0:
        return []

    query_kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if document_id:
        query_kwargs["where"] = {"documentId": document_id}

    result = collection.query(**query_kwargs)

    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    chunks: list[dict] = []
    for chunk_id, content, metadata, distance in zip(ids, documents, metadatas, distances):
        score = 1.0 - float(distance)
        chunks.append(
            {
                "chunkId": chunk_id,
                "documentId": metadata.get("documentId", ""),
                "title": metadata.get("title", "Course Document"),
                "sourcePath": metadata.get("sourcePath", ""),
                "page": int(metadata.get("page", 0)),
                "content": content,
                "score": score,
            }
        )
    return chunks


def list_documents() -> list[dict]:
    collection = get_collection()
    if collection.count() == 0:
        return []

    raw = collection.get(include=["metadatas"])
    documents: dict[str, dict] = {}
    for metadata in raw.get("metadatas", []):
        document_id = metadata.get("documentId") or "unknown"
        doc = documents.setdefault(
            document_id,
            {
                "documentId": document_id,
                "title": metadata.get("title") or "Course Document",
                "sourcePath": metadata.get("sourcePath") or "",
                "chunks": 0,
                "embeddedChunks": 0,
                "pages": set(),
            },
        )
        doc["chunks"] += 1
        doc["embeddedChunks"] += 1
        page = metadata.get("page")
        if page:
            doc["pages"].add(int(page))

    result = []
    for doc in documents.values():
        pages = sorted(doc.pop("pages"))
        doc["pageCount"] = len(pages)
        doc["firstPage"] = pages[0] if pages else None
        doc["lastPage"] = pages[-1] if pages else None
        result.append(doc)
    return sorted(result, key=lambda item: item["title"])


def status() -> dict:
    collection = get_collection()
    count = collection.count()
    return {
        "path": str(CHROMA_DIR),
        "collection": COLLECTION_NAME,
        "vectors": count,
        "documents": list_documents(),
    }
