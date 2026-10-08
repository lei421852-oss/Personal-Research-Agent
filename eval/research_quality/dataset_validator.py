"""来源和分组的确定性检查；不判断参考答案是否语义正确。"""

from collections import Counter, defaultdict
from pathlib import Path

from app.evidence.corpus import load_paper_corpus
from app.research.corpus_validation import (
    PreparedEvidence,
    SourceManifest,
    corpus_snapshot,
)
from eval.research_quality.schemas import QualityDataset


class BenchmarkValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Research quality benchmark validation failed")


def source_index(root: Path):
    papers, extra = root / "data/papers", root / "data/research_papers"
    corpus = load_paper_corpus(papers, additional_papers_dir=extra)
    records = {}
    for metadata in corpus.metadata:
        directory = papers / metadata.paper_key
        if not directory.exists():
            directory = extra / metadata.paper_key
        manifest = SourceManifest.model_validate_json(
            (directory / "source_manifest.json").read_text()
        )
        for line in (directory / "prepared_evidence.jsonl").read_text().splitlines():
            row = PreparedEvidence.model_validate_json(line)
            records[row.evidence_id] = (row, manifest)
    return records, corpus_snapshot(papers, corpus, extra)


def validate_dataset(dataset: QualityDataset, records, snapshot):
    dataset = QualityDataset.model_validate(dataset.model_dump(mode="python"))
    issues = []
    manifest = dataset.manifest
    papers = {p["paper_id"] for p in snapshot["papers"]}
    if set(manifest.registered_paper_ids) != papers or len(
        manifest.registered_paper_ids
    ) != len(papers):
        issues.append("manifest_registered_paper_set")
    if manifest.corpus_snapshot_id != snapshot["snapshot_id"]:
        issues.append("corpus_snapshot_mismatch")
    if not {"training", "prompt_optimization", "threshold_tuning"} <= set(
        manifest.forbidden_uses
    ):
        issues.append("missing_evaluation_isolation_policy")
    tasks = {"claim", "reviewer", "comparison", "abstention"}
    if set(manifest.case_files) != tasks or set(manifest.target_counts) != tasks:
        issues.append("manifest_task_set")
    counts = Counter(c.task_type for c in dataset.cases)
    if any(counts[t] != file.count for t, file in manifest.case_files.items()):
        issues.append("manifest_case_counts")
    identities = [c.case_id for c in dataset.cases]
    if len(set(identities)) != len(identities):
        issues.append("duplicate_case_id")
    groups, references = defaultdict(list), defaultdict(list)
    for case in dataset.cases:
        prefix = case.case_id + ":"
        if case.annotation_status == "approved_ai_reference" and (
            manifest.reference_kind != "ai_reviewed"
            or case.ai_review.corpus_snapshot_id != snapshot["snapshot_id"]
        ):
            issues.append(prefix + "ai_reference_kind_or_snapshot_mismatch")
        if (
            case.annotation_status == "approved_gold"
            and manifest.reference_kind != "human_gold"
        ):
            issues.append(prefix + "human_gold_in_ai_reference_dataset")
        if not set(case.paper_ids) <= papers:
            issues.append(prefix + "unknown_paper")
        if case.usage_partition != "benchmark_only":
            issues.append(prefix + "optimization_data_in_benchmark")
        for group in case.fact_group_ids:
            groups[group].append(case)
        if case.task_type in {"claim", "reviewer"}:
            references[" ".join(case.reference_answer.casefold().split())].append(case)
        for location in case.source_locations:
            pair = records.get(location.evidence_id)
            if pair is None:
                issues.append(prefix + "unknown_evidence")
                continue
            row, source = pair
            if location.paper_id not in case.paper_ids or (
                location.paper_id,
                location.pdf_page,
                location.section_title,
                location.source_document_sha256,
                location.source_version,
                location.source_url,
                location.printed_page,
            ) != (
                row.paper_id,
                row.page_number,
                row.section_title,
                source.source_sha256,
                source.source_version,
                source.source_url,
                source.page_mapping[str(row.page_number)],
            ):
                issues.append(prefix + "source_location_mismatch")
            if location.source_span != row.source_span:
                issues.append(prefix + "source_span_mismatch")
            text = row.caption if location.evidence_field == "caption" else row.content
            if (
                (location.evidence_field == "caption" and row.evidence_type != "figure")
                or text is None
                or location.quote not in text
            ):
                issues.append(prefix + "source_quote_mismatch")
        if case.comparison:
            for fact in case.comparison.paper_facts:
                if any(
                    i not in records or records[i][0].paper_id != fact.paper_id
                    for i in fact.evidence_ids
                ):
                    issues.append(prefix + "comparison_citation_wrong_paper")
            for difference in case.comparison.expected_differences:
                owners = {
                    records[i][0].paper_id
                    for i in difference.evidence_ids
                    if i in records
                }
                if owners != set(difference.paper_ids):
                    issues.append(prefix + "difference_missing_party_evidence")
        if case.abstention:
            target = case.abstention
            if target.checked_corpus_snapshot_id != snapshot["snapshot_id"]:
                issues.append(prefix + "boundary_snapshot_mismatch")
            if target.boundary_kind == "caption_only" and (
                target.assessment_scope != "provided_caption_only"
                or not case.source_locations
                or any(loc.evidence_field != "caption" for loc in case.source_locations)
            ):
                issues.append(prefix + "caption_boundary_scope")
            if target.boundary_kind == "unprepared_page":
                prepared = {(r.paper_id, r.page_number) for r, _ in records.values()}
                if target.requested_page is None or any(
                    (p, target.requested_page) in prepared for p in case.paper_ids
                ):
                    issues.append(prefix + "claimed_unprepared_page_is_available")
    for group, cases in groups.items():
        if len({c.split for c in cases if c.split != "unassigned"}) > 1:
            issues.append("split_leakage:" + group)
        if len({c.usage_partition for c in cases}) > 1:
            issues.append("usage_leakage:" + group)
    for cases in references.values():
        for index, case in enumerate(cases):
            for other in cases[index + 1 :]:
                if not set(case.fact_group_ids) & set(other.fact_group_ids):
                    issues.append(
                        "identical_reference_without_shared_group:" + case.case_id
                    )
    if issues:
        raise BenchmarkValidationError(sorted(set(issues)))
    approved = sum(c.annotation_status == "approved_gold" for c in dataset.cases)
    reviewed = sum(bool(c.annotation_provenance.human_reviews) for c in dataset.cases)
    return {
        "status": "structurally_valid",
        "dataset_version": manifest.dataset_version,
        "reference_kind": manifest.reference_kind,
        "total_cases": len(dataset.cases),
        "task_counts": dict(sorted(counts.items())),
        "candidate_count": sum(
            c.annotation_status in {"draft", "pending_review"} for c in dataset.cases
        ),
        "human_reviewed_count": reviewed,
        "approved_gold_count": approved,
        "approved_ai_reference_count": sum(
            c.annotation_status == "approved_ai_reference" for c in dataset.cases
        ),
        "rejected_count": sum(c.annotation_status == "rejected" for c in dataset.cases),
        "target_shortfall": {
            t: max(0, n - counts[t]) for t, n in manifest.target_counts.items()
        },
        "fact_group_count": len(groups),
        "corpus_snapshot_id": snapshot["snapshot_id"],
        "semantic_reference_validation": "ai_original_pdf_source_review_not_independent_human_gold"
        if manifest.reference_kind == "ai_reviewed"
        else "requires_human_original_pdf_review",
        "split_policy": "Explicit groups and normalized identical references checked; not a semantic paraphrase detector.",
        "real_provider_calls": 0,
    }
