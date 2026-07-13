from __future__ import annotations

import math
import re
from typing import Awaitable, Callable

from pydantic import BaseModel

from app.document_indexer import INDEX_PATH, list_documents, load_index
from app.vector_store import query_chunks, status as chroma_status

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


class Source(BaseModel):
    id: str
    title: str
    page: int
    content: str
    documentId: str | None = None
    score: float | None = None


FALLBACK_SOURCES = [
    Source(
        id="S1",
        title="Lecture 08 - Integration Techniques",
        page=12,
        content=(
            "Integration by parts follows from the product rule. "
            "The formula is integral u dv = uv - integral v du. "
            "Choose u as the part that becomes simpler when differentiated."
        ),
        documentId="fallback",
    ),
    Source(
        id="S2",
        title="Calculus Textbook - Chapter 7.1",
        page=231,
        content=(
            "For integral x cos(x) dx, choose u = x and dv = cos(x) dx. "
            "Then du = dx and v = sin(x), so the result is "
            "x sin(x) + cos(x) + C."
        ),
        documentId="fallback",
    ),
]

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "for", "from",
    "how", "i", "in", "is", "it", "of", "on", "or", "the", "this", "to",
    "what", "when", "with", "you", "your", "la", "là", "gi", "gì", "hay",
    "cho", "toi", "tôi", "mot", "một", "cach", "cách", "giai", "giải", "thich", "thích",
}


def _tokenize(text: str) -> list[str]:
    normalized = text.lower()
    normalized = normalized.replace("integration by parts", "integration_by_parts")
    tokens = re.findall(r"[a-zA-Z_À-ỹ0-9]+", normalized)
    return [token for token in tokens if len(token) > 1 and token not in STOPWORDS]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return -1.0
    return dot / (norm_a * norm_b)


def _lexical_score(chunk: dict, query_terms: set[str], query_text: str) -> float:
    source_text = f"{chunk.get('title', '')} {chunk.get('content', '')}".lower()
    source_terms = set(_tokenize(source_text))
    overlap = query_terms & source_terms
    score = float(len(overlap))

    if chunk.get("page", 0) < 50 or "contents" in source_text[:500]:
        score -= 8.0
    if "index" in source_text[:200]:
        score -= 4.0
    if "integration_by_parts" in query_terms and "integration by parts" in source_text:
        score += 6.0
    if "formula" in query_terms and "formula" in source_text:
        score += 2.0
    if "example" in query_terms and ("example" in source_text or "for instance" in source_text):
        score += 2.0
    if query_text and query_text in source_text:
        score += 5.0
    return score


def _as_source(source_id: str, chunk: dict, score: float) -> Source:
    return Source(
        id=source_id,
        title=str(chunk.get("title") or "Course Document"),
        page=int(chunk.get("page") or 0),
        content=str(chunk.get("content") or ""),
        documentId=str(chunk.get("documentId") or ""),
        score=round(float(score), 4),
    )


async def retrieve_sources(
    question: str,
    topic: str = "",
    top_k: int = 4,
    embedder: Embedder | None = None,
    document_id: str | None = None,
) -> list[Source]:
    query_text = f"{topic} {question}".strip()

    if embedder is not None:
        try:
            query_embedding = (await embedder([query_text]))[0]
            chroma_results = query_chunks(query_embedding, top_k=top_k, document_id=document_id)
            if chroma_results:
                return [
                    _as_source(f"S{index + 1}", chunk, float(chunk.get("score") or 0))
                    for index, chunk in enumerate(chroma_results)
                ]
        except Exception:
            # Keep chat usable if Chroma/Ollama embedding is unavailable.
            pass

    index = load_index()
    chunks = index.get("chunks", [])
    if document_id:
        chunks = [chunk for chunk in chunks if chunk.get("documentId") == document_id]
    if not chunks:
        return FALLBACK_SOURCES

    query_terms = set(_tokenize(query_text))
    if not query_terms:
        return FALLBACK_SOURCES

    embedded_chunks = [chunk for chunk in chunks if chunk.get("embedding")]
    ranked: list[tuple[float, dict]] = []
    if embedder is not None and embedded_chunks:
        try:
            query_embedding = (await embedder([query_text]))[0]
            ranked = [
                (_cosine_similarity(query_embedding, chunk.get("embedding") or []), chunk)
                for chunk in embedded_chunks
            ]
        except Exception:
            ranked = []

    if not ranked:
        ranked = [(_lexical_score(chunk, query_terms, query_text.lower()), chunk) for chunk in chunks]

    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = [(score, chunk) for score, chunk in ranked[:top_k] if score > 0]
    if not selected:
        return FALLBACK_SOURCES

    return [_as_source(f"S{index + 1}", chunk, score) for index, (score, chunk) in enumerate(selected)]


def build_source_context(sources: list[Source]) -> str:
    return "\n\n".join(
        f"[{source.id}] {source.title}, page {source.page}\n{source.content}"
        for source in sources
    )


def get_citations_from_answer(answer: str, sources: list[Source]) -> list[dict]:
    citations = []
    for source in sources:
        if f"[{source.id}]" in answer:
            citations.append(
                {
                    "sourceId": source.id,
                    "title": source.title,
                    "page": source.page,
                    "documentId": source.documentId,
                    "score": source.score,
                }
            )
    return citations


def rag_status() -> dict:
    index = load_index()
    chunks = index.get("chunks", [])
    embedded = [chunk for chunk in chunks if chunk.get("embedding")]
    chroma = chroma_status()
    chroma_vectors = chroma.get("vectors", 0)
    return {
        "indexPath": str(INDEX_PATH),
        "indexedChunks": chroma_vectors or len(chunks),
        "embeddedChunks": chroma_vectors or len(embedded),
        "embeddingModel": index.get("embeddingModel"),
        "retrievalMode": "chroma" if chroma_vectors else ("embedding-json" if embedded else "lexical-fallback"),
        "usingFallbackSources": (chroma_vectors == 0 and len(chunks) == 0),
        "vectorStore": chroma,
        "documents": list_documents(),
    }
