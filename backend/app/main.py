import re
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
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
from app.progress import build_deadlines, build_progress_report, build_study_plan
from app.sources import (
    Source,
    build_source_context,
    get_citations_from_answer,
    rag_status,
    retrieve_sources,
)
from app.storage import (
    add_chat_message,
    create_course,
    get_course,
    get_chat_messages,
    get_topic_mastery,
    list_courses,
    list_calendar_events,
    list_chat_sessions,
    list_quiz_attempts,
    list_topic_mastery,
    save_calendar_event,
    save_quiz_attempt,
    update_topic_mastery,
    upsert_chat_session,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

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
GOOGLE_TOKEN_PATH = Path(__file__).resolve().parents[1] / "data" / "google_calendar_token.json"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    course: str = "Calculus II"
    topic: str = "Integration by parts"
    sessionId: str | None = None
    courseId: str | None = None


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


class CalendarEventRequest(BaseModel):
    course: str = Field(..., min_length=1, max_length=120)
    topic: str = Field("General review", max_length=120)
    title: str = Field(..., min_length=1, max_length=160)
    type: str = Field("assignment", max_length=40)
    dueDate: str = Field(..., min_length=8, max_length=30)
    source: str = Field("Personal calendar", max_length=80)
    courseId: str | None = None


class CalendarEventResponse(BaseModel):
    status: str
    event: dict


class GoogleAuthResponse(BaseModel):
    status: str
    configured: bool
    clientId: str = ""
    authUrl: str = ""
    redirectUri: str = ""
    demoLogin: bool = False
    message: str = ""


class CourseCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=240)
    defaultTopic: str = Field("General review", max_length=120)


def build_messages(request: ChatRequest, sources: list[Source]) -> list[dict]:
    source_ids = ", ".join(f"[{source.id}]" for source in sources) or "no source ids"
    system_prompt = (
        "You are a Vietnamese academic tutor inside a personal AI tutoring tool. "
        "Your job is chat tutoring only: explain concepts, examples, hints, and study next steps. "
        "Do not handle document upload, do not grade quiz answers, and do not claim that you changed stored data. "
        "Final answer only; do not show reasoning or hidden analysis. "
        "Write in Vietnamese as one concise tutoring response with at most 4 short sentences. "
        "Use standard Vietnamese only. Never use Chinese, Japanese, Korean, Han characters, or mixed-language fragments. "
        "For C programming, translate pointer as 'con trỏ', address as 'địa chỉ', and dereference as 'truy cập giá trị qua con trỏ'. "
        "If a term sounds unnatural in Vietnamese, keep the English technical term in parentheses. "
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
- Use clear Vietnamese or English technical terms only. Do not mix in Chinese/Japanese/Korean/Han characters.
- For C programming: pointer = con trỏ, address = địa chỉ, dereference = truy cập giá trị qua con trỏ.
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


def _public_base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _google_redirect_uri(request: Request) -> str:
    # Use the current public URL so temporary Colab/Cloudflare domains do not require editing .env.
    return f"{_public_base_url(request)}/api/auth/google/callback"


def _google_auth_url(request: Request) -> tuple[str, str]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    redirect_uri = _google_redirect_uri(request)
    if not client_id:
        return "", redirect_uri
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile https://www.googleapis.com/auth/calendar.readonly",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}", redirect_uri


def _load_google_token() -> dict | None:
    if not GOOGLE_TOKEN_PATH.exists():
        return None
    try:
        return json.loads(GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_google_token(token: dict) -> None:
    GOOGLE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOOGLE_TOKEN_PATH.write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")


def _google_configured_for_calendar() -> bool:
    return bool(
        os.getenv("GOOGLE_CLIENT_ID", "").strip()
        and os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    )


async def _fetch_google_calendar_events(access_token: str, max_results: int = 20) -> list[dict]:
    params = {
        "calendarId": "primary",
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max_results),
        "timeMin": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Google Calendar API error: {response.text[:300]}")
    return response.json().get("items", [])


def _calendar_event_to_deadline(event: dict, fallback_course: str = "Current course") -> dict:
    start = event.get("start") or {}
    due_date = start.get("date") or str(start.get("dateTime") or "")[:10]
    title = event.get("summary") or "Google Calendar event"
    return {
        "course": fallback_course,
        "topic": title,
        "title": title,
        "type": "exam" if "exam" in title.lower() or "midterm" in title.lower() else "review",
        "dueDate": due_date,
        "source": "Google Calendar",
    }


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


@app.get("/api/courses")
async def get_courses() -> dict:
    return {"status": "ok", "courses": list_courses()}


@app.post("/api/courses")
async def add_course(request: CourseCreateRequest) -> dict:
    try:
        course = create_course(
            name=request.name,
            description=request.description,
            default_topic=request.defaultTopic,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Cannot create course: {exc}") from exc
    return {"status": "ok", "course": course}


@app.get("/api/auth/google/login", response_model=GoogleAuthResponse)
async def google_login(request: Request) -> GoogleAuthResponse:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    demo_login = os.getenv("ENABLE_DEMO_LOGIN", "").strip().lower() in {"1", "true", "yes", "on"}
    auth_url, redirect_uri = _google_auth_url(request)
    if not auth_url:
        return GoogleAuthResponse(
            status="config_required",
            configured=False,
            redirectUri=redirect_uri,
            demoLogin=demo_login,
            message=(
                "Google OAuth is not configured. Use teacher demo login for Colab, or set "
                "GOOGLE_CLIENT_ID after creating OAuth credentials in Google Cloud."
            ),
        )
    return GoogleAuthResponse(
        status="ok",
        configured=True,
        clientId=client_id,
        authUrl=auth_url,
        redirectUri=redirect_uri,
        demoLogin=demo_login,
    )


@app.get("/api/google/calendar/status")
async def google_calendar_status(request: Request) -> dict:
    configured = bool(os.getenv("GOOGLE_CLIENT_ID", "").strip())
    calendar_configured = _google_configured_for_calendar()
    connected = bool(_load_google_token())
    redirect_uri = _google_redirect_uri(request)
    return {
        "status": "ok",
        "configured": configured,
        "calendarConfigured": calendar_configured,
        "connected": connected,
        "redirectUri": redirect_uri,
        "javascriptOrigin": _public_base_url(request),
        "mode": "connected" if connected else ("oauth-ready" if calendar_configured else "config-required"),
        "message": (
            "Google Calendar is connected and can sync events."
            if connected
            else (
                "Google Calendar OAuth can be started."
                if calendar_configured
                else "Google Calendar sync needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            )
        ),
        "requiredScopes": ["openid", "email", "profile", "https://www.googleapis.com/auth/calendar.readonly"],
    }


@app.get("/api/auth/google/callback")
async def google_auth_callback(request: Request, code: str = "", error: str = "") -> RedirectResponse:
    if error:
        return RedirectResponse(url=f"/?googleCalendar=error&detail={error}")
    if not code:
        return RedirectResponse(url="/?googleCalendar=error&detail=missing_code")
    if not _google_configured_for_calendar():
        return RedirectResponse(url="/?googleCalendar=error&detail=missing_google_secret")

    payload = {
        "code": code,
        "client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        "redirect_uri": _google_redirect_uri(request),
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post("https://oauth2.googleapis.com/token", data=payload)
    if response.status_code >= 400:
        return RedirectResponse(url="/?googleCalendar=error&detail=token_exchange_failed")
    _save_google_token(response.json())
    return RedirectResponse(url="/?googleCalendar=connected")


@app.post("/api/google/calendar/sync")
async def sync_google_calendar(course: str = "Current course") -> dict:
    token = _load_google_token()
    if not token or not token.get("access_token"):
        raise HTTPException(status_code=401, detail="Google Calendar is not connected")

    events = await _fetch_google_calendar_events(token["access_token"])
    saved = []
    for event in events:
        deadline = _calendar_event_to_deadline(event, fallback_course=course)
        if not deadline.get("dueDate"):
            continue
        saved.append(
            save_calendar_event(
                course=deadline["course"],
                topic=deadline["topic"],
                title=deadline["title"],
                event_type=deadline["type"],
                due_date=deadline["dueDate"],
                source=deadline["source"],
            )
        )
    return {"status": "ok", "imported": len(saved), "events": saved}


@app.get("/api/documents")
async def get_documents(course: str | None = None) -> dict:
    rag = rag_status()
    if course:
        rag["documents"] = list_documents(course=course)
    return {"status": "ok", "documents": list_documents(course=course), "rag": rag}


@app.get("/api/chat/sessions")
async def get_chat_sessions(course: str | None = None) -> dict:
    return {"status": "ok", "sessions": list_chat_sessions(course=course)}


@app.get("/api/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> dict:
    messages = get_chat_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"status": "ok", "messages": messages}


@app.get("/api/practice/attempts")
async def get_practice_attempts(course: str | None = None) -> dict:
    return {"status": "ok", "attempts": list_quiz_attempts(course=course)}


@app.get("/api/progress/monitor")
async def get_progress_monitor(course: str | None = None) -> dict:
    return build_progress_report(
        list_quiz_attempts(limit=100, course=course),
        list_topic_mastery(course=course),
        list_calendar_events(course=course),
        course=course,
    )


@app.get("/api/study-plan")
async def get_study_plan(course: str | None = None) -> dict:
    progress_report = build_progress_report(
        list_quiz_attempts(limit=100, course=course),
        list_topic_mastery(course=course),
        list_calendar_events(course=course),
        course=course,
    )
    return build_study_plan(progress_report)


@app.get("/api/integrations/mock-deadlines")
async def get_mock_deadlines(course: str | None = None) -> dict:
    return {"status": "ok", "deadlines": build_deadlines(list_calendar_events(course=course), include_mock=True, course=course)}


@app.get("/api/calendar/events")
async def get_calendar_events(course: str | None = None) -> dict:
    return {"status": "ok", "events": list_calendar_events(course=course)}


@app.post("/api/calendar/events", response_model=CalendarEventResponse)
async def add_calendar_event(request: CalendarEventRequest) -> CalendarEventResponse:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", request.dueDate):
        raise HTTPException(status_code=400, detail="dueDate must use YYYY-MM-DD format")
    event = save_calendar_event(
        course=request.course,
        topic=request.topic,
        title=request.title,
        event_type=request.type,
        due_date=request.dueDate,
        source=request.source,
        course_id=request.courseId,
    )
    return CalendarEventResponse(status="ok", event=event)


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
    course: str = Form(""),
    courseId: str = Form(""),
) -> DocumentUploadResponse:
    saved_path = await _save_upload(file)
    skip_upload_embedding = os.getenv("SKIP_UPLOAD_EMBEDDING", "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        result = await index_document_file(
            file_path=saved_path,
            title=title.strip() or Path(file.filename or saved_path.name).stem,
            keywords=parse_keywords(keywords),
            embedder=None if skip_upload_embedding else ollama.embed_texts,
            embedding_model=None if skip_upload_embedding else ollama.embedding_model,
            replace_existing_document=True,
            course=course.strip() or None,
            course_id=courseId.strip() or None,
        )
        if skip_upload_embedding:
            result["embeddingError"] = "Embedding skipped for fast Colab upload; lexical retrieval is active."
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=f"Embedding model unavailable: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Document indexing failed: {exc}") from exc
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
        course=request.course,
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
            course_id=request.courseId,
        )
        return result

    query = f"{request.question.topic} {request.question.question}"
    sources = await retrieve_sources(
        query,
        request.question.topic,
        embedder=ollama.embed_texts,
        document_id=request.documentId,
        course=request.course,
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
        course_id=request.courseId,
    )
    return result


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    session_id = upsert_chat_session(request.sessionId, request.course, request.topic, request.message, request.courseId)
    add_chat_message(
        session_id,
        "user",
        request.message,
        {"course": request.course, "topic": request.topic},
    )
    sources = await retrieve_sources(request.message, request.topic, embedder=ollama.embed_texts, course=request.course)
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
    sources = await retrieve_sources(request.message, request.topic, embedder=ollama.embed_texts, course=request.course)

    async def token_stream():
        try:
            async for token in ollama.stream_chat(build_messages(request, sources)):
                yield token
        except OllamaError as exc:
            yield f"\n[Backend error: {exc}]"

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")


FRONTEND_DIR = Path(__file__).resolve().parents[2]
FRONTEND_ASSETS = {"app.js", "styles.css"}


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def serve_frontend_index() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found")
    return FileResponse(index_path)


@app.get("/{asset_name}", include_in_schema=False)
async def serve_frontend_asset(asset_name: str) -> FileResponse:
    if asset_name not in FRONTEND_ASSETS:
        raise HTTPException(status_code=404, detail="Frontend asset not found")
    asset_path = FRONTEND_DIR / asset_name
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="Frontend asset not found")
    return FileResponse(asset_path)
