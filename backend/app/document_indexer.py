from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Awaitable, Callable

from pypdf import PdfReader

from app.vector_store import list_documents as list_chroma_documents
from app.vector_store import delete_document as delete_chroma_document
from app.vector_store import replace_document, status as chroma_status

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
DEFAULT_INDEX_PATH = DATA_DIR / "rag_index.json"
LEGACY_INDEX_PATH = DATA_DIR / "calculus_chunks.json"
INDEX_PATH = Path(os.getenv("RAG_INDEX_PATH", str(DEFAULT_INDEX_PATH)))

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(text: str, max_words: int = 180, overlap_words: int = 40) -> list[str]:
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    step = max(1, max_words - overlap_words)
    while start < len(words):
        chunk = " ".join(words[start : start + max_words]).strip()
        if len(chunk) > 40:
            chunks.append(chunk)
        start += step
    return chunks


def parse_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def should_keep_page(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = text.lower()
    return any(keyword in lower for keyword in keywords)


def make_document_id(title: str, file_name: str) -> str:
    digest = hashlib.sha1(f"{title}:{file_name}".encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", Path(file_name).stem).strip("-").lower()[:40]
    return f"doc-{slug or 'document'}-{digest}"


def empty_index() -> dict:
    return {"version": 2, "embeddingModel": None, "chunks": []}


def _normalize_legacy_chunks(raw_chunks: list[dict]) -> dict:
    chunks = []
    for raw in raw_chunks:
        chunks.append(
            {
                "chunkId": str(raw.get("chunkId", "")),
                "documentId": str(raw.get("documentId", "legacy-calculus")),
                "title": str(raw.get("title", "Course Document")),
                "sourcePath": str(raw.get("sourcePath", "")),
                "page": int(raw.get("page", 0)),
                "content": str(raw.get("content", "")),
                "embedding": raw.get("embedding"),
            }
        )
    return {"version": 2, "embeddingModel": None, "chunks": chunks}


def load_index() -> dict:
    if INDEX_PATH.exists():
        raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return _normalize_legacy_chunks(raw)
        raw.setdefault("version", 2)
        raw.setdefault("embeddingModel", None)
        raw.setdefault("chunks", [])
        return raw

    if LEGACY_INDEX_PATH.exists():
        raw = json.loads(LEGACY_INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return _normalize_legacy_chunks(raw)

    return empty_index()


def save_index(index: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_pages(file_path: Path) -> tuple[list[dict], list[dict]]:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_pages(file_path)
    if suffix in {".txt", ".md"}:
        text = normalize_text(file_path.read_text(encoding="utf-8", errors="ignore"))
        return ([{"page": 1, "text": text}] if text else []), []
    raise ValueError(f"Unsupported document type: {suffix}. Supported: .pdf, .txt, .md")


def extract_pdf_pages(file_path: Path) -> tuple[list[dict], list[dict]]:
    reader = PdfReader(str(file_path))
    pages: list[dict] = []
    skipped: list[dict] = []

    for index, page in enumerate(reader.pages, start=1):
        try:
            text = normalize_text(page.extract_text() or "")
        except Exception as exc:
            skipped.append({"page": index, "error": str(exc)})
            continue
        if text:
            pages.append({"page": index, "text": text})

    return pages, skipped


def list_documents() -> list[dict]:
    chroma_documents = list_chroma_documents()
    index = load_index()
    documents: dict[str, dict] = {
        chroma_doc["documentId"]: chroma_doc for chroma_doc in chroma_documents
    }
    chroma_document_ids = set(documents)

    for chunk in index.get("chunks", []):
        document_id = chunk.get("documentId") or "unknown"
        if document_id in chroma_document_ids:
            continue
        doc = documents.setdefault(
            document_id,
            {
                "documentId": document_id,
                "title": chunk.get("title") or "Course Document",
                "sourcePath": chunk.get("sourcePath") or "",
                "chunks": 0,
                "embeddedChunks": 0,
                "pages": set(),
            },
        )
        doc["chunks"] += 1
        if chunk.get("embedding"):
            doc["embeddedChunks"] += 1
        if chunk.get("page"):
            doc["pages"].add(chunk.get("page"))

    result = []
    for doc in documents.values():
        pages = sorted(doc.pop("pages", []))
        if pages:
            doc["pageCount"] = len(pages)
            doc["firstPage"] = pages[0]
            doc["lastPage"] = pages[-1]
        result.append(doc)
    return sorted(result, key=lambda item: item["title"])


def delete_document(document_id: str, delete_file: bool = True) -> dict:
    index = load_index()
    chunks = index.get("chunks", [])
    removed_chunks = [chunk for chunk in chunks if chunk.get("documentId") == document_id]
    kept_chunks = [chunk for chunk in chunks if chunk.get("documentId") != document_id]

    index["chunks"] = kept_chunks
    save_index(index)

    vector_store = delete_chroma_document(document_id)
    deleted_files: list[str] = []

    if delete_file:
        candidate_paths = {
            str(chunk.get("sourcePath") or "")
            for chunk in removed_chunks
            if chunk.get("sourcePath")
        }
        remaining_paths = {
            str(chunk.get("sourcePath") or "")
            for chunk in kept_chunks
            if chunk.get("sourcePath")
        }
        for raw_path in candidate_paths - remaining_paths:
            path = Path(raw_path)
            try:
                resolved = path.resolve()
                upload_root = UPLOAD_DIR.resolve()
                if resolved.exists() and upload_root in resolved.parents:
                    resolved.unlink()
                    deleted_files.append(str(resolved))
            except OSError:
                pass

    return {
        "documentId": document_id,
        "deletedChunks": len(removed_chunks),
        "deletedFiles": deleted_files,
        "vectorStore": vector_store,
    }


async def index_document_file(
    file_path: Path,
    title: str | None = None,
    keywords: list[str] | None = None,
    embedder: Embedder | None = None,
    embedding_model: str | None = None,
    replace_existing_document: bool = True,
) -> dict:
    title = title or file_path.stem
    keywords = keywords or []
    document_id = make_document_id(title, file_path.name)

    pages, skipped_pages = extract_pages(file_path)
    kept_pages = [page for page in pages if should_keep_page(page["text"], keywords)]

    chunks: list[dict] = []
    for page in kept_pages:
        for chunk_index, content in enumerate(chunk_text(page["text"]), start=1):
            chunks.append(
                {
                    "chunkId": f"{document_id}-p{page['page']}-c{chunk_index}",
                    "documentId": document_id,
                    "title": title,
                    "sourcePath": str(file_path),
                    "page": page["page"],
                    "content": content,
                    "embedding": None,
                }
            )

    embedding_error = None
    if chunks and embedder is not None:
        try:
            embeddings = await embedder([chunk["content"] for chunk in chunks])
            if len(embeddings) != len(chunks):
                raise RuntimeError("Embedding count does not match chunk count")
            for chunk, embedding in zip(chunks, embeddings):
                chunk["embedding"] = embedding
        except Exception as exc:
            embedding_error = str(exc)

    index = load_index()
    if replace_existing_document:
        index["chunks"] = [chunk for chunk in index.get("chunks", []) if chunk.get("documentId") != document_id]
    index.setdefault("chunks", []).extend(chunks)
    if embedding_model:
        index["embeddingModel"] = embedding_model
    save_index(index)

    vector_store = None
    if chunks and any(chunk.get("embedding") for chunk in chunks):
        vector_store = replace_document(document_id, chunks, embedding_model)

    return {
        "documentId": document_id,
        "title": title,
        "sourcePath": str(file_path),
        "pagesScanned": len(pages),
        "pagesKept": len(kept_pages),
        "pagesSkipped": len(skipped_pages),
        "chunksWritten": len(chunks),
        "embeddedChunks": sum(1 for chunk in chunks if chunk.get("embedding")),
        "embeddingModel": embedding_model,
        "embeddingError": embedding_error,
        "skippedPages": skipped_pages[:10],
        "indexPath": str(INDEX_PATH),
        "vectorStore": vector_store or chroma_status(),
    }
