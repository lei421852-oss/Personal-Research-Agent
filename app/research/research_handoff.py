"""主 Agent 到既有研究链的结构化交接；不从答案文字推断研究对象。"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, model_validator

from app.evidence.models import NonEmpty
from app.research.models import ResearchModel


class ResearchToolInput(ResearchModel):
    research_goal: NonEmpty = Field(max_length=4000)
    selected_paper_ids: list[NonEmpty] = Field(min_length=1, max_length=3)
    selection_source: Literal["retrieved", "named", "explicit_scope"]
    selection_rationale: dict[str, NonEmpty]

    @model_validator(mode="after")
    def unique_scope(self):
        if len(set(self.selected_paper_ids)) != len(self.selected_paper_ids):
            raise ValueError("duplicate_research_paper")
        if set(self.selection_rationale) != set(self.selected_paper_ids):
            raise ValueError("rationale_must_match_scope")
        return self


class ResearchHandoff(ResearchModel):
    original_question: NonEmpty
    execution_question: NonEmpty
    research_goal: NonEmpty
    selected_paper_ids: list[NonEmpty]
    selection_rationale: dict[str, NonEmpty]
    selection_source: str
    retrieval_queries: list[str]
    candidate_references: list[dict]
    paper_availability: dict[str, bool]
    research_constraints: dict
    budget_context: dict


@dataclass
class ResearchContext:
    """每个请求专属的观察账本，只保存公开工具结果，不保存隐藏推理。"""

    original_question: str
    execution_question: str
    catalog: object
    prepared_ids: set[str]
    explicit_scope: list[str] = field(default_factory=list)
    observations: list = field(default_factory=list)
    reports: dict = field(default_factory=dict)
    delivered_reports: set[str] = field(default_factory=set)
    handoffs: list = field(default_factory=list)
    research_calls: int = 0
    agent_result: object = None

    def candidates(self):
        from app.papers.candidates import union_candidates

        return union_candidates(self.observations)

    def handoff(self, arguments, budget):
        identities = arguments.selected_paper_ids
        if any(identity not in self.catalog.documents for identity in identities):
            raise ValueError("unknown_research_paper")
        if self.explicit_scope and not set(identities).issubset(self.explicit_scope):
            raise ValueError("research_outside_explicit_scope")
        candidates = {item["paper_id"]: item for item in self.candidates()}
        if arguments.selection_source == "retrieved":
            if any(identity not in candidates for identity in identities):
                raise ValueError("research_paper_was_not_retrieved")
        elif arguments.selection_source == "explicit_scope":
            if not self.explicit_scope:
                raise ValueError("no_explicit_scope_was_supplied")
        else:
            # 只验证模型提出的命名来源，不据此决定是否检索或启动研究。
            names = {
                record.document.paper_id: record.name for record in self.catalog.records
            }
            for identity in identities:
                name = re.escape(names[identity])
                if not re.search(
                    r"(?<!\w)" + name + r"(?!\w)", self.original_question, re.IGNORECASE
                ):
                    raise ValueError("named_paper_not_in_original_question")
        return ResearchHandoff(
            original_question=self.original_question,
            execution_question=self.execution_question,
            research_goal=arguments.research_goal,
            selected_paper_ids=identities,
            selection_rationale=arguments.selection_rationale,
            selection_source=arguments.selection_source,
            retrieval_queries=list(
                dict.fromkeys(
                    o.payload.get("query", "")
                    for o in self.observations
                    if o.tool_name == "search_papers"
                )
            ),
            candidate_references=[candidates[i] for i in identities if i in candidates],
            paper_availability={i: i in self.prepared_ids for i in identities},
            research_constraints={
                "explicit_scope": self.explicit_scope,
                "no_external_download": True,
                "coverage_worker_enabled": False,
            },
            budget_context=budget.snapshot(),
        )
