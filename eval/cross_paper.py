"""版本化研究评测契约；来源标签仅供评分，绝不传入 Worker。"""

from typing import Literal

from pydantic import Field, model_validator

from app.evidence.models import NonEmpty
from app.research.corpus_validation import SHA256, canonical_hash
from app.research.models import ResearchModel, ResearchPlan
from app.research.requirements import expand_requirements, group_worker_tasks


class CrossPaperCase(ResearchModel):
    case_id: NonEmpty
    category: Literal[
        "single_fact",
        "two_paper",
        "three_paper",
        "insufficient",
        "false_premise",
        "caption_pixels",
        "contamination",
        "recovery",
    ]
    plan: ResearchPlan
    expected_behavior: NonEmpty
    source_labels: dict[str, list[NonEmpty]]
    controlled_only: bool = False

    @model_validator(mode="after")
    def labels(self):
        requirements = {r.requirement_id for r in expand_requirements(self.plan)}
        if set(self.source_labels) != requirements:
            raise ValueError(
                "Source labels must correspond exactly to the planned requirements"
            )
        if any(len(ids) != len(set(ids)) for ids in self.source_labels.values()):
            raise ValueError("Duplicate source label")
        if self.category in {"contamination", "recovery"} and not self.controlled_only:
            raise ValueError(
                "Attack/recovery fixtures are controlled engineering cases"
            )
        return self

    def worker_tasks(self):
        # 问题和维度来自计划；期待行为、来源标签不进入运行时输入。
        return group_worker_tasks(expand_requirements(self.plan))


class CrossPaperDataset(ResearchModel):
    schema_version: Literal[1] = 1
    version: Literal["stage-e-v1"] = "stage-e-v1"
    corpus_snapshot_id: SHA256
    original_plan: ResearchPlan
    original_plan_sha256: SHA256
    core_requirement_ids: list[NonEmpty] = Field(min_length=15, max_length=15)
    annotation_status: Literal["source_checked_candidates"] = (
        "source_checked_candidates"
    )
    cases: list[CrossPaperCase]

    @model_validator(mode="after")
    def frozen_core(self):
        if (
            canonical_hash(self.original_plan.model_dump(mode="json"))
            != self.original_plan_sha256
        ):
            raise ValueError("Original plan hash mismatch")
        originals = expand_requirements(self.original_plan)
        if self.core_requirement_ids != [r.requirement_id for r in originals]:
            raise ValueError("Original 15 requirements changed")
        ids = [c.case_id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate evaluation case")
        actual = [
            r
            for c in self.cases
            if c.case_id.startswith("core-")
            for r in expand_requirements(c.plan)
        ]
        if [r.model_dump() for r in actual] != [r.model_dump() for r in originals]:
            raise ValueError(
                "Core cases must preserve the original requirement questions and identities"
            )
        return self

    def validate_corpus(self, corpus, snapshot_id):
        if snapshot_id != self.corpus_snapshot_id:
            raise ValueError("Evaluation corpus snapshot changed")
        evidence = {
            e.evidence_id: e for e in corpus.text_evidence + corpus.figure_evidence
        }
        for case in self.cases:
            for req in expand_requirements(case.plan):
                for identity in case.source_labels[req.requirement_id]:
                    if (
                        identity not in evidence
                        or evidence[identity].paper_id != req.paper_id
                    ):
                        raise ValueError(
                            "Source label is missing or crosses paper scope"
                        )


def retrieval_metrics(cases, retriever):
    """每个来源标注需求是一个样本；无正向标签的缺失题不进入召回率分母。"""
    rows = []
    for case in cases:
        for req in expand_requirements(case.plan):
            relevant = set(case.source_labels[req.requirement_id])
            if not relevant:
                continue
            hits = retriever.search_text(req.question, [req.paper_id], 10)
            if any(h.evidence.paper_id != req.paper_id for h in hits):
                raise ValueError("Retrieval escaped paper scope")
            ids = [h.evidence.evidence_id for h in hits]
            rows.append(
                {
                    "case_id": case.case_id,
                    "requirement_id": req.requirement_id,
                    "paper_id": req.paper_id,
                    "retrieved_evidence_ids": ids,
                    "relevant_evidence_ids": sorted(relevant),
                    "recall_at_5": len(set(ids[:5]) & relevant) / len(relevant),
                    "recall_at_10": len(set(ids[:10]) & relevant) / len(relevant),
                }
            )
    return {
        "sample_count": len(rows),
        "recall_at_5": sum(r["recall_at_5"] for r in rows) / len(rows)
        if rows
        else None,
        "recall_at_10": sum(r["recall_at_10"] for r in rows) / len(rows)
        if rows
        else None,
        "cases": rows,
    }


def audit_report(source, report):
    """只判断权威记录和跨论文引用结构，不冒充自然语言蕴含验证。"""
    allowed = {c.claim_id: c for c in source.verified_claims}
    if (
        report.verified_claims != source.verified_claims
        or report.missing_evidence != source.missing_evidence
    ):
        raise ValueError("Report rewrote authoritative facts or gaps")
    if (
        report.paper_ids != source.plan.paper_ids
        or report.question != source.plan.question
    ):
        raise ValueError("Report changed research scope")
    from app.research.synthesis_validator import SynthesisOutputValidator

    # 重用冻结的完整网格校验器，额外覆盖落盘报告遭到篡改的场景。
    SynthesisOutputValidator().validate(
        {
            "sections": [
                {
                    "dimension_id": s.dimension_id,
                    "cells": [
                        c.model_dump(mode="json")
                        | {
                            "evidence_ids": allowed[c.claim_id].evidence_ids
                            if c.claim_id in allowed
                            else []
                        }
                        for c in s.cells
                    ],
                    "comparisons": [c.model_dump(mode="json") for c in s.comparisons],
                }
                for s in report.sections
            ]
        },
        source,
    )
    comparisons = [c for s in report.sections for c in s.comparisons]
    for section in report.sections:
        for comparison in section.comparisons:
            claims = [allowed.get(i) for i in comparison.claim_ids]
            if any(c is None or c.dimension_id != section.dimension_id for c in claims):
                raise ValueError(
                    "Comparison references unverified/wrong-dimension claim"
                )
            papers = {c.paper_id for c in claims}
            evidence = {e for c in claims for e in c.evidence_ids}
            if (
                len(papers) < 2
                or len(comparison.paper_ids) != len(papers)
                or set(comparison.paper_ids) != papers
                or len(comparison.claim_ids) != len(set(comparison.claim_ids))
                or len(comparison.evidence_ids) != len(evidence)
                or set(comparison.evidence_ids) != evidence
            ):
                raise ValueError("Comparison lacks distinct reviewed paper sources")
    return {
        "cross_paper_comparison_count": len(comparisons),
        "cross_paper_citation_validity_rate": 1.0 if comparisons else None,
        "comparison_detection_method": "Structural claim membership, paper scope, dimension and exact evidence-union checks; not semantic entailment.",
    }


def research_metrics(
    outcome, *, initial_workers, initial_reviews, wall_time_ms, corpus_report
):
    report, source = outcome.report, outcome.synthesis_input
    if report is None:
        raise ValueError("A synthesis report is required")
    audit = audit_report(source, report)
    rejected_claims = {
        (r.requirement_id, r.claim, tuple(r.evidence_ids))
        for r in [*initial_reviews, *outcome.merged.review_results]
        if r.status == "rejected"
    }
    return {
        "corpus_validity_rate": corpus_report["corpus_validity_rate"],
        "evidence_provenance_validity_rate": corpus_report[
            "evidence_provenance_validity_rate"
        ],
        "retrieval_recall_at_5": None,
        "retrieval_recall_at_10": None,
        "worker_supported_count": sum(
            r.status == "supported" for w in initial_workers for r in w.requirements
        ),
        "reviewer_passed_count": sum(r.status == "passed" for r in initial_reviews),
        "reviewer_rejected_count": sum(r.status == "rejected" for r in initial_reviews),
        "recovery_attempted_count": outcome.metrics.recovery_attempted_count,
        "recovery_converted_count": outcome.metrics.recovery_passed_count,
        "verified_coverage": report.metrics.evidence_coverage,
        "stage_d_baseline_coverage": 5 / 15,
        "coverage_delta_vs_stage_d": report.metrics.evidence_coverage - 5 / 15
        if len(source.cells) == 15
        else None,
        **audit,
        "unsupported_claim_count": len(rejected_claims),
        "unsupported_claim_detection_method": "Distinct claims rejected by model Reviewer among initial/final snapshots; structured citation violations fail closed. This is a reviewer diagnostic, NOT a human/independent hallucination rate.",
        "total_model_calls": outcome.metrics.research_usage.model_calls,
        "total_tool_calls": outcome.metrics.research_usage.tool_calls,
        "wall_time_ms": wall_time_ms,
        "resolution_rate": report.metrics.resolution_rate,
        "review_pass_rate": report.metrics.review_pass_rate,
        "report_generation_success_rate": report.metrics.report_generation_success_rate,
        "report_status": report.status,
    }
