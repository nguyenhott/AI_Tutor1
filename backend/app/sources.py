from pydantic import BaseModel


class Source(BaseModel):
    id: str
    title: str
    page: int
    content: str


SOURCES = [
    Source(
        id="S1",
        title="Lecture 08 - Integration Techniques",
        page=12,
        content=(
            "Integration by parts follows from the product rule. "
            "The formula is integral u dv = uv - integral v du. "
            "Choose u as the part that becomes simpler when differentiated."
        ),
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
    ),
]


def build_source_context() -> str:
    return "\n\n".join(
        f"[{source.id}] {source.title}, page {source.page}\n{source.content}"
        for source in SOURCES
    )


def get_citations_from_answer(answer: str) -> list[dict]:
    citations = []
    for source in SOURCES:
        if f"[{source.id}]" in answer:
            citations.append(
                {
                    "sourceId": source.id,
                    "title": source.title,
                    "page": source.page,
                }
            )
    return citations
