from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ollama_client import OllamaClient, OllamaError
from app.sources import build_source_context, get_citations_from_answer


app = FastAPI(
    title="Personal AI Tutoring Tool Backend",
    version="0.1.0",
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


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict]
    model: str


def build_messages(request: ChatRequest) -> list[dict]:
    system_prompt = (
        "You are a Vietnamese academic tutor. Final answer only; do not show reasoning or analysis. "
        "Keep the answer under 4 short sentences. "
        "Explain clearly and step by step. "
        "Use only the provided SOURCES for course-specific facts. "
        "When using a source, cite it exactly as [S1] or [S2]. "
        "Do not invent citations."
    )

    user_prompt = f"""
COURSE: {request.course}
TOPIC: {request.topic}

SOURCES:
{build_source_context()}

QUESTION:
{request.message}

RESPONSE REQUIREMENTS:
- Answer in Vietnamese.
- Keep it very concise. Do not include your reasoning process.
- Cite sources as [S1] or [S2] when using the provided sources.
- If the sources are not enough, say the course material is not enough.
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/model/health")
async def model_health() -> dict:
    try:
        tags = await ollama.check_connection()
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "model": ollama.model, "ollama": tags}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        answer = await ollama.chat(build_messages(request))
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(
        answer=answer,
        citations=get_citations_from_answer(answer),
        model=ollama.model,
    )


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    async def token_stream():
        try:
            async for token in ollama.stream_chat(build_messages(request)):
                yield token
        except OllamaError as exc:
            yield f"\n[Backend error: {exc}]"

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")



