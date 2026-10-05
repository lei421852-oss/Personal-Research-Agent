"""Small retrieval-only questions; no answer generation or formal benchmark."""

import json
from pathlib import Path

from pydantic import Field

from app.evidence.corpus import PaperCorpus
from app.evidence.fixtures import FixtureModel, SmokeQuery
from app.evidence.models import NonEmpty


class EvidenceRequest(SmokeQuery):
    paper_key: NonEmpty


class CorpusSmokeQuestion(FixtureModel):
    question_id: NonEmpty
    question: NonEmpty
    requests: list[EvidenceRequest] = Field(min_length=1)


def load_smoke_questions(path: Path, corpus: PaperCorpus) -> list[CorpusSmokeQuestion]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported smoke-question schema")
    questions = [CorpusSmokeQuestion.model_validate(q) for q in data["questions"]]
    if len({q.question_id for q in questions}) != len(questions):
        raise ValueError("Duplicate smoke-question ID")
    selected = {m.paper_key: {p.page for p in m.pages} for m in corpus.metadata}
    for question in questions:
        for request in question.requests:
            if (
                request.paper_key not in selected
                or not set(request.expected_pages) <= selected[request.paper_key]
            ):
                raise ValueError(
                    "Smoke request refers to unknown paper or unselected page"
                )
    return questions
