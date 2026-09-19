from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean


MOCK_DEADLINES = [
    {
        "id": "mock-lms-c-programming-pointer-lab",
        "course": "C Programming",
        "title": "Pointer practice lab",
        "topic": "Pointers in C",
        "type": "assignment",
        "dueDate": "2026-09-02",
        "source": "Mock LMS",
    },
    {
        "id": "mock-calendar-circuits-quiz",
        "course": "Electric Circuits",
        "title": "Circuit analysis quiz",
        "topic": "Ohm's law",
        "type": "quiz",
        "dueDate": "2026-09-04",
        "source": "Mock Calendar",
    },
    {
        "id": "mock-lms-calculus-problem-set",
        "course": "Calculus II",
        "title": "Integration problem set",
        "topic": "Integration by parts",
        "type": "assignment",
        "dueDate": "2026-09-06",
        "source": "Mock LMS",
    },
]

DEFAULT_PROGRESS = [
    {
        "course": "C Programming",
        "topic": "Pointers in C",
        "mastery": 46,
        "averageScore": 46,
        "attempts": 0,
        "trend": "needs_data",
        "lastPracticed": None,
    },
    {
        "course": "Electric Circuits",
        "topic": "Ohm's law",
        "mastery": 58,
        "averageScore": 58,
        "attempts": 0,
        "trend": "needs_data",
        "lastPracticed": None,
    },
]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _days_until(value: str | None, today: date | None = None) -> int | None:
    due_date = _parse_date(value)
    if not due_date:
        return None
    return (due_date - (today or date.today())).days


def _normalize_score(score: float | int | None) -> int:
    value = float(score or 0)
    if value <= 1:
        value *= 100
    return max(0, min(100, round(value)))


def _deadline_priority(deadline: dict, today: date | None = None) -> int:
    days = _days_until(deadline.get("dueDate"), today)
    if days is None:
        return 20
    if days < 0:
        return 0
    event_type = (deadline.get("type") or "").lower()
    exam_boost = 10 if event_type in {"exam", "midterm", "final"} else 0
    return max(1, 14 - days) + exam_boost


def _matching_deadline(course: str, topic: str, deadlines: list[dict]) -> dict | None:
    course_lower = course.lower()
    topic_lower = topic.lower()
    matches = [
        deadline
        for deadline in deadlines
        if deadline.get("course", "").lower() == course_lower
        and (
            deadline.get("topic", "").lower() == topic_lower
            or topic_lower in deadline.get("title", "").lower()
            or deadline.get("topic", "").lower() in topic_lower
        )
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: _days_until(item.get("dueDate")) or 99)[0]


def normalize_deadline(event: dict) -> dict:
    return {
        "id": event.get("id") or event.get("eventId") or event.get("title") or "calendar-event",
        "course": event.get("course") or "Current course",
        "title": event.get("title") or "Study event",
        "topic": event.get("topic") or "General review",
        "type": event.get("type") or event.get("event_type") or "assignment",
        "dueDate": event.get("dueDate") or event.get("due_date") or "",
        "source": event.get("source") or "Personal calendar",
    }


def build_deadlines(calendar_events: list[dict] | None = None, include_mock: bool = True) -> list[dict]:
    deadlines = [normalize_deadline(event) for event in (calendar_events or [])]
    if include_mock:
        deadlines.extend(MOCK_DEADLINES)
    return sorted(
        [deadline for deadline in deadlines if deadline.get("dueDate")],
        key=lambda item: item["dueDate"],
    )


def build_progress_report(
    attempts: list[dict],
    mastery_rows: list[dict],
    deadlines: list[dict] | None = None,
) -> dict:
    deadlines = build_deadlines(deadlines, include_mock=True)
    by_topic: dict[tuple[str, str], dict] = {}

    for row in mastery_rows:
        key = (row.get("course") or "Current course", row.get("topic") or "Current topic")
        by_topic[key] = {
            "course": key[0],
            "topic": key[1],
            "mastery": int(row.get("mastery") or 0),
            "scores": [],
            "attempts": 0,
            "lastPracticed": row.get("updated_at"),
        }

    for attempt in attempts:
        key = (attempt.get("course") or "Current course", attempt.get("topic") or "Current topic")
        item = by_topic.setdefault(
            key,
            {
                "course": key[0],
                "topic": key[1],
                "mastery": 46,
                "scores": [],
                "attempts": 0,
                "lastPracticed": None,
            },
        )
        item["scores"].append(_normalize_score(attempt.get("score")))
        item["attempts"] += 1
        item["lastPracticed"] = max(
            [value for value in [item.get("lastPracticed"), attempt.get("created_at")] if value],
            default=None,
        )

    if not by_topic:
        for item in DEFAULT_PROGRESS:
            by_topic[(item["course"], item["topic"])] = {**item, "scores": []}

    topics = []
    for item in by_topic.values():
        scores = item.pop("scores", [])
        average_score = round(mean(scores)) if scores else int(item.get("mastery") or 0)
        mastery = int(item.get("mastery") or average_score)
        recent_score = scores[0] if scores else average_score
        trend = "stable"
        if scores and recent_score < average_score - 10:
            trend = "down"
        elif scores and recent_score > average_score + 10:
            trend = "up"
        elif not scores:
            trend = "needs_data"
        topics.append(
            {
                **item,
                "mastery": mastery,
                "averageScore": average_score,
                "recentScore": recent_score,
                "trend": trend,
                "riskLevel": "high" if mastery < 50 else ("medium" if mastery < 70 else "low"),
            }
        )

    weak_topics = sorted(
        [topic for topic in topics if topic["mastery"] < 70 or topic["trend"] == "down"],
        key=lambda item: (item["mastery"], item["averageScore"]),
    )
    mastered = [topic for topic in topics if topic["mastery"] >= 80]
    average = round(mean([topic["mastery"] for topic in topics])) if topics else 0

    alerts = []
    for topic in weak_topics[:3]:
        deadline = _matching_deadline(topic["course"], topic["topic"], deadlines)
        deadline_text = ""
        if deadline:
            days = _days_until(deadline["dueDate"])
            event_label = "exam" if deadline.get("type") == "exam" else "deadline"
            deadline_text = f" Upcoming {event_label}: {deadline['title']} in {max(days or 0, 0)} days."
        alerts.append(
            {
                "severity": topic["riskLevel"],
                "course": topic["course"],
                "topic": topic["topic"],
                "mastery": topic["mastery"],
                "message": (
                    f"{topic['topic']} needs attention at {topic['mastery']}% mastery."
                    f"{deadline_text}"
                ),
                "action": "Start a focused tutoring and practice session",
                "deadline": deadline,
            }
        )

    return {
        "status": "ok",
        "summary": {
            "averageMastery": average,
            "topicsTracked": len(topics),
            "topicsMastered": len(mastered),
            "weakTopics": len(weak_topics),
            "alerts": len(alerts),
        },
        "topics": sorted(topics, key=lambda item: (item["course"], item["topic"])),
        "weakTopics": weak_topics,
        "alerts": alerts,
        "deadlines": deadlines,
    }


def build_study_plan(progress_report: dict, available_minutes: int = 270) -> dict:
    weak_topics = progress_report.get("weakTopics") or []
    deadlines = progress_report.get("deadlines") or MOCK_DEADLINES
    today = date.today()
    days = ["MON", "TUE", "WED", "THU", "FRI"]
    plan_items = []

    priority_topics = weak_topics[:]
    if not priority_topics:
        priority_topics = sorted(
            progress_report.get("topics") or DEFAULT_PROGRESS,
            key=lambda item: item.get("mastery", 100),
        )[:2]

    minutes_per_item = max(25, min(45, available_minutes // max(1, min(5, len(priority_topics) + 1))))

    for index, topic in enumerate(priority_topics[:4]):
        deadline = _matching_deadline(topic["course"], topic["topic"], deadlines)
        day_date = today + timedelta(days=index)
        priority_score = (100 - int(topic.get("mastery", 0))) + (
            _deadline_priority(deadline) if deadline else 0
        )
        plan_items.append(
            {
                "day": days[index],
                "date": day_date.isoformat(),
                "course": topic["course"],
                "topic": topic["topic"],
                "minutes": minutes_per_item + (10 if priority_score > 60 else 0),
                "priority": "High" if priority_score > 60 else "Medium",
                "activity": "Review concept -> worked example -> adaptive quiz",
                "reason": f"Mastery is {topic.get('mastery', 0)}%",
                "deadline": deadline,
            }
        )

    if len(plan_items) < 5:
        for deadline in sorted(deadlines, key=lambda item: item["dueDate"]):
            if len(plan_items) >= 5:
                break
            if any(item["topic"] == deadline.get("topic") and item["course"] == deadline.get("course") for item in plan_items):
                continue
            day_date = today + timedelta(days=len(plan_items))
            event_type = (deadline.get("type") or "assignment").lower()
            priority = "High" if event_type in {"exam", "midterm", "final"} or _deadline_priority(deadline) > 12 else "Medium"
            activity = (
                f"Exam review for {deadline['title']}"
                if event_type in {"exam", "midterm", "final"}
                else f"Prepare for {deadline['title']}"
            )
            plan_items.append(
                {
                    "day": days[len(plan_items)],
                    "date": day_date.isoformat(),
                    "course": deadline["course"],
                    "topic": deadline["topic"],
                    "minutes": 40 if priority == "High" else 25,
                    "priority": priority,
                    "activity": activity,
                    "reason": f"Upcoming {deadline['type']} from {deadline['source']}",
                    "deadline": deadline,
                }
            )

    rationale = "The plan prioritizes weak topics first, then upcoming mock LMS/calendar deadlines."
    if weak_topics:
        first = weak_topics[0]
        rationale = (
            f"{first['topic']} is first because mastery is {first['mastery']}%. "
            "The rest of the week balances weak-topic repair with upcoming deadlines."
        )

    return {
        "status": "ok",
        "availableMinutes": available_minutes,
        "items": plan_items,
        "rationale": rationale,
        "deadlines": deadlines,
    }
