from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DB_PATH = Path(os.getenv("LEARNING_DB_PATH", str(DATA_DIR / "tutorflow.sqlite")))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                course TEXT NOT NULL,
                topic TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id TEXT PRIMARY KEY,
                course TEXT NOT NULL,
                topic TEXT NOT NULL,
                question_json TEXT NOT NULL,
                answer TEXT NOT NULL,
                result_json TEXT NOT NULL,
                correct INTEGER NOT NULL,
                score REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS topic_mastery (
                course TEXT NOT NULL,
                topic TEXT NOT NULL,
                mastery INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(course, topic)
            );

            CREATE TABLE IF NOT EXISTS calendar_events (
                id TEXT PRIMARY KEY,
                course TEXT NOT NULL,
                topic TEXT NOT NULL,
                title TEXT NOT NULL,
                event_type TEXT NOT NULL,
                due_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'Personal calendar',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def upsert_chat_session(
    session_id: str | None,
    course: str,
    topic: str,
    first_message: str,
) -> str:
    init_db()
    title = first_message.strip().replace("\n", " ")[:80] or topic or "New chat"

    with _connect() as connection:
        if session_id:
            existing = connection.execute(
                "SELECT id FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE chat_sessions
                    SET course = ?, topic = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (course, topic, session_id),
                )
                return session_id

        new_id = uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO chat_sessions (id, course, topic, title)
            VALUES (?, ?, ?, ?)
            """,
            (new_id, course, topic, title),
        )
        return new_id


def add_chat_message(
    session_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
    init_db()
    message_id = uuid.uuid4().hex
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages (id, session_id, role, content, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, session_id, role, content, _json(metadata or {})),
        )
        connection.execute(
            "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        row = connection.execute(
            "SELECT * FROM chat_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_chat_sessions(limit: int = 30) -> list[dict]:
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, course, topic, title, created_at, updated_at
            FROM chat_sessions
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_chat_messages(session_id: str) -> list[dict]:
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, session_id, role, content, metadata_json, created_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()

    messages = []
    for row in rows:
        message = _row_to_dict(row)
        try:
            message["metadata"] = json.loads(message.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            message["metadata"] = {}
        messages.append(message)
    return messages


def get_topic_mastery(course: str, topic: str, default: int = 46) -> int:
    init_db()
    with _connect() as connection:
        row = connection.execute(
            "SELECT mastery FROM topic_mastery WHERE course = ? AND topic = ?",
            (course, topic),
        ).fetchone()
    return int(row["mastery"]) if row else default


def update_topic_mastery(course: str, topic: str, mastery: int) -> int:
    init_db()
    mastery = max(0, min(100, int(mastery)))
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO topic_mastery (course, topic, mastery)
            VALUES (?, ?, ?)
            ON CONFLICT(course, topic)
            DO UPDATE SET mastery = excluded.mastery, updated_at = CURRENT_TIMESTAMP
            """,
            (course, topic, mastery),
        )
    return mastery


def list_topic_mastery(limit: int = 100) -> list[dict]:
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT course, topic, mastery, updated_at
            FROM topic_mastery
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def save_quiz_attempt(
    course: str,
    topic: str,
    question: dict,
    answer: str,
    result: dict,
) -> dict:
    init_db()
    attempt_id = uuid.uuid4().hex
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO quiz_attempts
            (id, course, topic, question_json, answer, result_json, correct, score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                course,
                topic,
                _json(question),
                answer,
                _json(result),
                1 if result.get("correct") else 0,
                float(result.get("score") or 0),
            ),
        )
        row = connection.execute(
            "SELECT id, course, topic, correct, score, created_at FROM quiz_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_quiz_attempts(limit: int = 30) -> list[dict]:
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, course, topic, correct, score, created_at
            FROM quiz_attempts
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def save_calendar_event(
    course: str,
    topic: str,
    title: str,
    event_type: str,
    due_date: str,
    source: str = "Personal calendar",
) -> dict:
    init_db()
    event_id = uuid.uuid4().hex
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO calendar_events
            (id, course, topic, title, event_type, due_date, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                course.strip() or "Current course",
                topic.strip() or "General review",
                title.strip() or "Study event",
                event_type.strip() or "assignment",
                due_date.strip(),
                source.strip() or "Personal calendar",
            ),
        )
        row = connection.execute(
            """
            SELECT id, course, topic, title, event_type, due_date, source, created_at, updated_at
            FROM calendar_events
            WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_calendar_events(limit: int = 50) -> list[dict]:
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, course, topic, title, event_type, due_date, source, created_at, updated_at
            FROM calendar_events
            ORDER BY due_date ASC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]
