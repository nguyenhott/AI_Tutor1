from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from pydantic import BaseModel, Field

from app.sources import Source

QuizGenerator = Callable[[list[dict], float], Awaitable[str]]
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

LANGUAGE_RULES = (
    "Language constraint: use Vietnamese or English only. "
    "Never use Chinese, Japanese, Korean, Han characters, or mixed Chinese/Vietnamese text. "
    "If the source text is English, keep technical terms in English and explain them in Vietnamese."
)


class PracticeRequest(BaseModel):
    course: str = "Calculus II"
    topic: str = "Integration by parts"
    prompt: str = ""
    documentId: str | None = None
    mastery: int = Field(default=46, ge=0, le=100)
    count: int = Field(default=4, ge=1, le=10)
    difficulty: str = "auto"
    questionType: str = "mixed"


class PracticeQuestion(BaseModel):
    id: str
    type: str
    level: str
    topic: str
    question: str
    choices: list[str] = []
    correctAnswer: str = ""
    expectedKeywords: list[str] = []
    explanation: str
    sourceIds: list[str] = []


class PracticeResponse(BaseModel):
    status: str
    generationMode: str = "llm_rag"
    course: str
    topic: str
    recommendedLevel: str
    questions: list[PracticeQuestion]
    retrievedSources: list[dict]


class PracticeSubmitRequest(BaseModel):
    question: PracticeQuestion
    answer: str
    course: str = "Calculus II"
    documentId: str | None = None
    currentMastery: int = Field(default=46, ge=0, le=100)


class PracticeSubmitResponse(BaseModel):
    status: str
    correct: bool
    score: float
    feedback: str
    explanation: str
    correctAnswer: str = ""
    hint: str = ""
    missingConcepts: list[str] = []
    newMastery: int


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("'", "")
    return re.sub(r"\s+", " ", text)


def recommended_level(mastery: int) -> str:
    if mastery < 50:
        return "Level 1 - Foundation"
    if mastery < 75:
        return "Level 2 - Apply"
    return "Level 3 - Transfer"


def selected_level(mastery: int, difficulty: str = "auto") -> str:
    normalized = _normalize(difficulty)
    if normalized == "easy":
        return "Level 1 - Foundation"
    if normalized == "medium":
        return "Level 2 - Apply"
    if normalized == "hard":
        return "Level 3 - Transfer"
    return recommended_level(mastery)


def selected_question_types(question_type: str) -> str:
    normalized = _normalize(question_type)
    if normalized in {"multiple_choice", "multiple choice", "mcq"}:
        return "multiple_choice only"
    if normalized in {"short_answer", "short answer", "free_text", "free text"}:
        return "short_answer only"
    return "a balanced mix of multiple_choice and short_answer"


def _source_ids(sources: list[Source]) -> list[str]:
    return [source.id for source in sources[:2]]


def _source_summary(sources: list[Source]) -> str:
    if not sources:
        return "No retrieved sources. The evaluator must mark course-material support as insufficient."

    return "\n\n".join(
        (
            f"[{source.id}] {source.title}, page {source.page}\n"
            f"{source.content[:1200]}"
        )
        for source in sources[:4]
    )


def _keywords_from_topic(topic: str) -> list[str]:
    return [token for token in re.findall(r"[a-zA-Z0-9]+", topic.lower()) if len(token) > 2][:4]


def _keywords_from_text(text: str, topic: str) -> list[str]:
    stopwords = {
        "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
        "you", "your", "can", "will", "into", "when", "then", "than", "have", "has",
        "using", "use", "used", "page", "chapter", "section",
    }
    topic_words = _keywords_from_topic(topic)
    words = [
        word
        for word in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(word) > 3 and word not in stopwords
    ]
    result = []
    for word in topic_words + words:
        if word not in result:
            result.append(word)
        if len(result) >= 5:
            break
    return result or topic_words or ["concept"]


def _sentence_candidates(sources: list[Source], topic: str) -> list[tuple[Source, str]]:
    candidates: list[tuple[Source, str]] = []
    topic_terms = set(_keywords_from_topic(topic))
    for source in sources:
        sentences = re.split(r"(?<=[.!?])\s+", source.content)
        for sentence in sentences:
            clean = re.sub(r"\s+", " ", sentence).strip()
            if len(clean) < 45:
                continue
            lower = clean.lower()
            score = sum(1 for term in topic_terms if term in lower)
            if topic_terms and score == 0:
                continue
            candidates.append((source, clean[:260]))

    if candidates:
        return candidates

    for source in sources:
        clean = re.sub(r"\s+", " ", source.content).strip()
        if clean:
            candidates.append((source, clean[:260]))
    return candidates


def _fallback_question_type(request: PracticeRequest, index: int) -> str:
    selected = selected_question_types(request.questionType)
    if selected.startswith("multiple_choice"):
        return "multiple_choice"
    if selected.startswith("short_answer"):
        return "short_answer"
    return "multiple_choice" if index % 2 == 0 else "short_answer"


def _extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return a JSON object")

    return json.loads(cleaned[start : end + 1])


def _clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _has_cjk(text: str) -> bool:
    return bool(CJK_PATTERN.search(text or ""))


def _language_safe_text(text: object, fallback: str) -> str:
    value = str(text or "").strip()
    if not value or _has_cjk(value):
        return fallback
    return value


def _language_safe_list(value: object, fallback: list[str]) -> list[str]:
    items = [item for item in _clean_list(value) if not _has_cjk(item)]
    return items or fallback


def _coerce_generated_question(
    raw: dict,
    request: PracticeRequest,
    index: int,
    source_ids: list[str],
) -> PracticeQuestion:
    question_type = str(raw.get("type") or "short_answer").strip()
    if question_type not in {"multiple_choice", "short_answer"}:
        question_type = "short_answer"

    choices = _clean_list(raw.get("choices"))
    correct_answer = str(raw.get("correctAnswer") or "").strip()
    expected_keywords = _clean_list(raw.get("expectedKeywords"))

    if question_type == "multiple_choice":
        if len(choices) < 2:
            raise ValueError("Generated multiple-choice question has too few choices")
        if not correct_answer:
            raise ValueError("Generated multiple-choice question is missing correctAnswer")
        if correct_answer not in choices:
            choices.append(correct_answer)
    else:
        choices = []
        if not expected_keywords:
            expected_keywords = _keywords_from_topic(request.topic or request.prompt)

    question_text = str(raw.get("question") or "").strip()
    if not question_text:
        raise ValueError("Generated question is empty")
    if _has_cjk(question_text):
        raise ValueError("Generated question used a disallowed language")

    explanation = str(raw.get("explanation") or "").strip()
    explanation = _language_safe_text(
        explanation,
        "Xem lại tài liệu nguồn liên quan và so sánh câu trả lời với ý chính cần nắm.",
    )
    choices = [choice for choice in choices if not _has_cjk(choice)]
    correct_answer = _language_safe_text(correct_answer, "")
    expected_keywords = _language_safe_list(expected_keywords, _keywords_from_topic(request.topic or request.prompt))

    if question_type == "multiple_choice":
        if len(choices) < 2 or not correct_answer:
            raise ValueError("Generated multiple-choice question used a disallowed language")
        if correct_answer not in choices:
            choices.append(correct_answer)

    return PracticeQuestion(
        id=f"ai-{uuid.uuid4().hex[:10]}-{index + 1}",
        type=question_type,
        level=str(raw.get("level") or selected_level(request.mastery, request.difficulty)).strip(),
        topic=str(raw.get("topic") or request.topic or "Current topic").strip(),
        question=question_text,
        choices=choices,
        correctAnswer=correct_answer,
        expectedKeywords=expected_keywords,
        explanation=explanation,
        sourceIds=source_ids,
    )


def _serialize_sources(sources: list[Source]) -> list[dict]:
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


def _fallback_practice_response(
    request: PracticeRequest,
    sources: list[Source],
    level: str,
    reason: str,
) -> PracticeResponse:
    source_ids = _source_ids(sources)
    topic = request.topic or request.prompt or "Current topic"
    candidates = _sentence_candidates(sources, topic)
    questions: list[PracticeQuestion] = []

    if not candidates:
        candidates = [
            (
                Source(
                    id="S1",
                    title="Course material",
                    page=0,
                    content="No relevant source text was retrieved for this topic.",
                    documentId=request.documentId or "",
                ),
                "No relevant source text was retrieved for this topic.",
            )
        ]

    for index in range(request.count):
        source, sentence = candidates[index % len(candidates)]
        question_type = _fallback_question_type(request, index)
        keywords = _keywords_from_text(sentence, topic)
        explanation = (
            f"Generated from {source.title}, page {source.page}. "
            f"Fallback was used because the model response was not usable ({reason})."
        )

        if question_type == "multiple_choice":
            correct = sentence
            choices = [
                correct,
                f"{topic} is unrelated to the selected course material.",
                "The uploaded material does not contain any usable learning content.",
                "The best answer is to ignore the source and use outside knowledge.",
            ]
            shift = index % len(choices)
            choices = choices[shift:] + choices[:shift]
            questions.append(
                PracticeQuestion(
                    id=f"fallback-{uuid.uuid4().hex[:10]}-{index + 1}",
                    type="multiple_choice",
                    level=level,
                    topic=topic,
                    question=(
                        f"Which statement is supported by {source.title}"
                        f"{f', page {source.page}' if source.page else ''}?"
                    ),
                    choices=choices,
                    correctAnswer=correct,
                    expectedKeywords=keywords,
                    explanation=explanation,
                    sourceIds=[source.id],
                )
            )
        else:
            questions.append(
                PracticeQuestion(
                    id=f"fallback-{uuid.uuid4().hex[:10]}-{index + 1}",
                    type="short_answer",
                    level=level,
                    topic=topic,
                    question=(
                        f"Explain the key idea about '{topic}' from {source.title}"
                        f"{f', page {source.page}' if source.page else ''}."
                    ),
                    choices=[],
                    correctAnswer=sentence,
                    expectedKeywords=keywords,
                    explanation=explanation,
                    sourceIds=[source.id],
                )
            )

    return PracticeResponse(
        status="ok",
        generationMode="fallback_rag",
        course=request.course,
        topic=request.topic,
        recommendedLevel=level,
        questions=questions,
        retrievedSources=_serialize_sources(sources),
    )


async def generate_practice_with_llm(
    request: PracticeRequest,
    sources: list[Source],
    quiz_generator: QuizGenerator,
) -> PracticeResponse:
    level = selected_level(request.mastery, request.difficulty)
    source_ids = _source_ids(sources)
    system_prompt = (
        "You are a strict assessment designer for an AI tutoring system. "
        f"{LANGUAGE_RULES} "
        "Generate practice questions using ONLY the provided course sources. "
        "Respect the requested number of questions, difficulty, and question type. "
        "Every question must be answerable from the sources. "
        "Each generated question must test the requested topic, not general background knowledge. "
        "Return ONLY valid JSON. Do not include markdown, comments, or explanations outside JSON. "
        "Do not invent facts, citations, or source content."
    )
    user_prompt = f"""
COURSE: {request.course}
TOPIC: {request.topic}
STUDENT QUESTION: {request.prompt}
CURRENT MASTERY: {request.mastery}/100
RECOMMENDED DIFFICULTY: {level}
NUMBER OF QUESTIONS: {request.count}
QUESTION TYPE: {selected_question_types(request.questionType)}

SOURCES:
{_source_summary(sources)}

JSON SCHEMA:
{{
  "questions": [
    {{
      "type": "multiple_choice" or "short_answer",
      "level": "{level}",
      "topic": "{request.topic}",
      "question": "question text",
      "choices": ["A", "B", "C", "D"],
      "correctAnswer": "exact correct answer for multiple_choice; short target answer for short_answer",
      "expectedKeywords": ["keyword1", "keyword2"],
      "explanation": "short explanation"
    }}
  ]
}}

RULES:
- Generate exactly {request.count} questions.
- Follow QUESTION TYPE exactly.
- Questions must be answerable from SOURCES.
- Use Vietnamese for question, feedback-oriented text, and explanation. English technical terms from SOURCES are allowed.
- Do not use Chinese characters or Chinese sentences anywhere in the JSON.
- For multiple_choice, correctAnswer must exactly match one item in choices.
- For short_answer, correctAnswer must be a concise reference answer and expectedKeywords must contain 3 to 5 grading keywords.
- Match the recommended difficulty and avoid overly broad questions.
- Include enough explanation for the learner to understand the correct answer.
- If SOURCES are insufficient, return a single short_answer asking the learner to upload or select relevant material, with explanation saying the sources are insufficient.
"""
    try:
        raw_answer = await quiz_generator(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            0.1,
        )
        parsed = _extract_json_object(raw_answer)
    except Exception as exc:
        return _fallback_practice_response(request, sources, level, f"invalid model JSON: {exc}")

    raw_questions = parsed.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        return _fallback_practice_response(request, sources, level, "missing questions array")

    questions = []
    for index, raw in enumerate(raw_questions[: request.count]):
        if not isinstance(raw, dict):
            continue
        try:
            questions.append(_coerce_generated_question(raw, request, index, source_ids))
        except ValueError:
            continue
    if not questions:
        return _fallback_practice_response(request, sources, level, "no usable generated questions")

    return PracticeResponse(
        status="ok",
        generationMode="llm_rag",
        course=request.course,
        topic=request.topic,
        recommendedLevel=level,
        questions=questions,
        retrievedSources=_serialize_sources(sources),
    )


def grade_answer(request: PracticeSubmitRequest) -> PracticeSubmitResponse:
    question = request.question
    answer = _normalize(request.answer)

    if question.type == "multiple_choice":
        correct = _normalize(question.correctAnswer) == answer
        score = 1.0 if correct else 0.0
    else:
        keywords = [_normalize(keyword) for keyword in question.expectedKeywords if keyword.strip()]
        hits = [keyword for keyword in keywords if keyword and keyword in answer]
        score = len(hits) / max(1, len(keywords))
        correct = score >= 0.5

    if correct:
        feedback = "Dung. Ban nam duoc y chinh."
        hint = ""
        new_mastery = min(100, request.currentMastery + 8)
    elif score > 0:
        feedback = "Gan dung. Cau tra loi co mot phan y dung nhung con thieu diem quan trong."
        hint = "Doc lai giai thich va bo sung y con thieu."
        new_mastery = max(0, request.currentMastery + 2)
    else:
        feedback = "Chua dung. Hay xem lai dap an va thu mot vi du don gian hon."
        hint = "Bat dau tu dinh nghia chinh, sau do so sanh voi tai lieu nguon."
        new_mastery = max(0, request.currentMastery - 5)

    return PracticeSubmitResponse(
        status="ok",
        correct=correct,
        score=round(score, 2),
        feedback=feedback,
        explanation=question.explanation,
        correctAnswer=question.correctAnswer,
        hint=hint,
        missingConcepts=[],
        newMastery=new_mastery,
    )


def _mastery_after_score(current_mastery: int, score: float) -> int:
    if score >= 0.8:
        return min(100, current_mastery + 8)
    if score >= 0.45:
        return min(100, current_mastery + 2)
    return max(0, current_mastery - 5)


async def evaluate_answer_with_llm(
    request: PracticeSubmitRequest,
    sources: list[Source],
    evaluator: QuizGenerator,
) -> PracticeSubmitResponse:
    question = request.question
    if question.type == "multiple_choice":
        return grade_answer(request)

    system_prompt = (
        "You are a strict but helpful Vietnamese tutor. "
        f"{LANGUAGE_RULES} "
        "Evaluate the student's short answer using ONLY the provided source material, "
        "the question, the expected answer, and the rubric keywords. "
        "Do not use outside knowledge to rescue an answer when the provided sources are insufficient. "
        "Return ONLY valid JSON. Do not include markdown. "
        "Be fair: give partial credit when the core idea is present and grounded in the sources."
    )
    user_prompt = f"""
QUESTION:
{question.question}

EXPECTED ANSWER:
{question.correctAnswer}

RUBRIC KEYWORDS:
{", ".join(question.expectedKeywords)}

STUDENT ANSWER:
{request.answer}

SOURCES:
{_source_summary(sources)}

JSON SCHEMA:
{{
  "correct": true,
  "score": 0.0,
  "feedback": "short feedback in Vietnamese",
  "explanation": "explain the correct idea in Vietnamese",
  "missingConcepts": ["concept1", "concept2"],
  "hint": "one short hint in Vietnamese"
}}

RULES:
- score must be between 0 and 1.
- correct is true only if score >= 0.7.
- feedback must mention what is correct and what is missing.
- explanation must be grounded in SOURCES and must not introduce unsupported facts.
- hint must help the student retry without giving a long solution.
- feedback, explanation, missingConcepts, and hint must use Vietnamese or English only.
- Do not use Chinese characters or Chinese sentences anywhere in the JSON.
- If SOURCES are insufficient, set correct to false, score at most 0.3, and explain that the uploaded material is insufficient.
"""
    raw_answer = await evaluator(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        0.1,
    )
    parsed = _extract_json_object(raw_answer)
    score = max(0.0, min(1.0, float(parsed.get("score", 0))))
    correct = bool(parsed.get("correct", score >= 0.7))
    if score < 0.7:
        correct = False

    return PracticeSubmitResponse(
        status="ok",
        correct=correct,
        score=round(score, 2),
        feedback=_language_safe_text(
            parsed.get("feedback"),
            "Câu trả lời cần xem lại. Hãy so sánh với đáp án tham khảo và bổ sung các ý còn thiếu.",
        ),
        explanation=_language_safe_text(
            parsed.get("explanation"),
            _language_safe_text(
                question.explanation,
                "Câu trả lời cần bám vào ý chính trong đáp án tham khảo và tài liệu nguồn.",
            ),
        ),
        correctAnswer=question.correctAnswer,
        hint=_language_safe_text(
            parsed.get("hint"),
            "Đọc lại phần đáp án tham khảo, sau đó trả lời bằng các ý chính trong tài liệu.",
        ),
        missingConcepts=_language_safe_list(parsed.get("missingConcepts"), question.expectedKeywords[:3]),
        newMastery=_mastery_after_score(request.currentMastery, score),
    )
