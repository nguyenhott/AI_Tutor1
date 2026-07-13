import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.assessment import (
    PracticeRequest,
    PracticeResponse,
    PracticeSubmitRequest,
    PracticeSubmitResponse,
    evaluate_answer_with_llm,
    generate_practice_with_llm,
    grade_answer,
)
from app.document_indexer import (
    UPLOAD_DIR,
    delete_document,
    index_document_file,
    list_documents,
    parse_keywords,
)
from app.ollama_client import OllamaClient, OllamaError
from app.sources import (
    Source,
    build_source_context,
    get_citations_from_answer,
    rag_status,
    retrieve_sources,
)
from app.storage import (
    add_chat_message,
    get_chat_messages,
    get_topic_mastery,
    list_chat_sessions,
    list_quiz_attempts,
    save_quiz_attempt,
    update_topic_mastery,
    upsert_chat_session,
)


app = FastAPI(
    title="Personal AI Tutoring Tool Backend",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ollama = OllamaClient()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    course: str = "Calculus II"
    topic: str = "Integration by parts"
    sessionId: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict]
    model: str
    retrievedSources: list[dict]
    sessionId: str


class DocumentUploadResponse(BaseModel):
    status: str
    document: dict
    rag: dict


class DocumentDeleteResponse(BaseModel):
    status: str
    document: dict
    rag: dict


def build_messages(request: ChatRequest, sources: list[Source]) -> list[dict]:
    source_ids = ", ".join(f"[{source.id}]" for source in sources) or "no source ids"
    system_prompt = (
        "You are a Vietnamese academic tutor inside a personal AI tutoring tool. "
        "Your job is chat tutoring only: explain concepts, examples, hints, and study next steps. "
        "Do not handle document upload, do not grade quiz answers, and do not claim that you changed stored data. "
        "Final answer only; do not show reasoning or hidden analysis. "
        "Write in Vietnamese as one concise tutoring response with at most 4 short sentences. "
        "Use only the provided SOURCES for course-specific facts; if the sources are not enough, say the course material is not enough. "
        f"When using a source, cite it exactly with one of these ids: {source_ids}. "
        "Do not invent facts, citations, page numbers, formulas, or source ids."
    )

    user_prompt = f"""
COURSE: {request.course}
TOPIC: {request.topic}

SOURCES:
{build_source_context(sources)}

QUESTION:
{request.message}

RESPONSE REQUIREMENTS:
- Answer in Vietnamese.
- Keep it very concise, at most 4 short sentences. Do not include your reasoning process.
- Cite sources with ids shown in SOURCES, for example [S1] or [S2].
- If the retrieved sources are not enough, say the course material is not enough.
- Do not answer as the upload tool or grading tool; stay in tutor chat mode.
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def serialize_sources(sources: list[Source]) -> list[dict]:
    return [
        {
            "sourceId": source.id,
            "title": source.title,
            "page": source.page,
            "documentId": source.documentId,
            "score": source.score,
        }
        for source in sources
    ]


def _safe_filename(filename: str) -> str:
    stem = Path(filename).stem or "document"
    suffix = Path(filename).suffix.lower()
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-")[:80] or "document"
    return f"{safe_stem}{suffix}"


async def _save_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="Only .pdf, .txt, and .md files are supported now")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_DIR / _safe_filename(file.filename or "document")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    destination.write_bytes(content)
    return destination


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/model/health")
async def model_health() -> dict:
    try:
        tags = await ollama.check_connection()
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "ok",
        "model": ollama.model,
        "embeddingModel": ollama.embedding_model,
        "ollama": tags,
        "rag": rag_status(),
    }


@app.get("/api/rag/status")
async def get_rag_status() -> dict:
    return {"status": "ok", "rag": rag_status()}


@app.get("/api/documents")
async def get_documents() -> dict:
    return {"status": "ok", "documents": list_documents(), "rag": rag_status()}


@app.get("/api/chat/sessions")
async def get_chat_sessions() -> dict:
    return {"status": "ok", "sessions": list_chat_sessions()}


@app.get("/api/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> dict:
    messages = get_chat_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"status": "ok", "messages": messages}


@app.get("/api/practice/attempts")
async def get_practice_attempts() -> dict:
    return {"status": "ok", "attempts": list_quiz_attempts()}


@app.delete("/api/documents/{document_id}", response_model=DocumentDeleteResponse)
async def remove_document(document_id: str) -> DocumentDeleteResponse:
    document_ids = {document["documentId"] for document in list_documents()}
    if document_id not in document_ids:
        raise HTTPException(status_code=404, detail="Document not found")

    result = delete_document(document_id)
    return DocumentDeleteResponse(status="ok", document=result, rag=rag_status())


@app.post("/api/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    keywords: str = Form(""),
) -> DocumentUploadResponse:
    saved_path = await _save_upload(file)
    result = await index_document_file(
        file_path=saved_path,
        title=title.strip() or Path(file.filename or saved_path.name).stem,
        keywords=parse_keywords(keywords),
        embedder=ollama.embed_texts,
        embedding_model=ollama.embedding_model,
        replace_existing_document=True,
    )
    return DocumentUploadResponse(status="ok", document=result, rag=rag_status())


@app.post("/api/practice/recommend", response_model=PracticeResponse)
async def recommend_practice_questions(request: PracticeRequest) -> PracticeResponse:
    request = request.model_copy(
        update={"mastery": get_topic_mastery(request.course, request.topic, request.mastery)}
    )
    query = request.prompt or request.topic
    sources = await retrieve_sources(
        query,
        request.topic,
        embedder=ollama.embed_texts,
        document_id=request.documentId,
    )
    try:
        return await generate_practice_with_llm(request, sources, ollama.chat)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cannot generate practice questions: {exc}") from exc


@app.post("/api/practice/submit", response_model=PracticeSubmitResponse)
async def submit_practice_answer(request: PracticeSubmitRequest) -> PracticeSubmitResponse:
    if request.question.type == "multiple_choice":
        result = grade_answer(request)
        update_topic_mastery(request.course, request.question.topic, result.newMastery)
        save_quiz_attempt(
            request.course,
            request.question.topic,
            request.question.model_dump(),
            request.answer,
            result.model_dump(),
        )
        return result

    query = f"{request.question.topic} {request.question.question}"
    sources = await retrieve_sources(
        query,
        request.question.topic,
        embedder=ollama.embed_texts,
        document_id=request.documentId,
    )
    try:
        result = await evaluate_answer_with_llm(request, sources, ollama.chat)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cannot evaluate short answer: {exc}") from exc
    update_topic_mastery(request.course, request.question.topic, result.newMastery)
    save_quiz_attempt(
        request.course,
        request.question.topic,
        request.question.model_dump(),
        request.answer,
        result.model_dump(),
    )
    return result


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    session_id = upsert_chat_session(request.sessionId, request.course, request.topic, request.message)
    add_chat_message(
        session_id,
        "user",
        request.message,
        {"course": request.course, "topic": request.topic},
    )
    sources = await retrieve_sources(request.message, request.topic, embedder=ollama.embed_texts)
    try:
        answer = await ollama.chat(build_messages(request, sources))
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    citations = get_citations_from_answer(answer, sources)
    retrieved_sources = serialize_sources(sources)
    add_chat_message(
        session_id,
        "assistant",
        answer,
        {
            "citations": citations,
            "retrievedSources": retrieved_sources,
            "model": ollama.model,
        },
    )

    return ChatResponse(
        answer=answer,
        citations=citations,
        model=ollama.model,
        retrievedSources=retrieved_sources,
        sessionId=session_id,
    )


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    sources = await retrieve_sources(request.message, request.topic, embedder=ollama.embed_texts)

    async def token_stream():
        try:
            async for token in ollama.stream_chat(build_messages(request, sources)):
                yield token
        except OllamaError as exc:
            yield f"\n[Backend error: {exc}]"

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")
