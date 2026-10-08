"""将已逐条完成的AI原文复核登记为可用参考集；不生成或假定审核结果。"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.research.corpus_validation import canonical_hash
from eval.research_quality.dataset_loader import ROOT, load_dataset
from eval.research_quality.dataset_validator import source_index, validate_dataset
from eval.research_quality.schemas import (
    QualityCase,
    QualityDataset,
    QualityManifest,
    record_sha256,
    reference_content_sha256,
)

SOURCE_DATASET = ROOT / "data/benchmark/research_quality_v1_ai_reviewed"
REVIEW_RECORDS = ROOT / "docs/acceptance/stage_f1_sample_review/case_reviews.jsonl"
OUTPUT_DATASET = ROOT / "data/benchmark/research_quality_v1_ai_reference"
EXPECTED_CASES = 90
# 按既有事实关联组件划分；不使用模型结果或检索指标选择dev样本。
DEV_COMPONENT_ANCHORS = (
    "rqv1-claim-blip2-image-backbones",
    "rqv1-abstention-clip-symmetric-loss-answerable",
    "rqv1-abstention-blip-caption-rgb",
    "rqv1-claim-blip-capfilt-initialization",
)


def grouped_splits(cases):
    parent = {c.case_id: c.case_id for c in cases}

    def find(identity):
        while parent[identity] != identity:
            parent[identity] = parent[parent[identity]]
            identity = parent[identity]
        return identity

    owners = {}
    for case in cases:
        for group in case.fact_group_ids:
            if group in owners:
                parent[find(case.case_id)] = find(owners[group])
            owners[group] = case.case_id
    if not set(DEV_COMPONENT_ANCHORS) <= set(parent):
        raise ValueError("Missing fixed split anchor")
    dev_roots = {find(i) for i in DEV_COMPONENT_ANCHORS}
    return {c.case_id: "dev" if find(c.case_id) in dev_roots else "test" for c in cases}


def build_reference_dataset(dataset, review_rows):
    if len(dataset.cases) != EXPECTED_CASES:
        raise ValueError("Reference finalization requires all 90 reviewed cases")
    reviews = {r["case_id"]: r for r in review_rows}
    if len(reviews) != len(review_rows) or set(reviews) != {
        c.case_id for c in dataset.cases
    }:
        raise ValueError("Review records missing, duplicated or unknown")
    splits = grouped_splits(dataset.cases)
    cases = []
    for case in dataset.cases:
        row = reviews[case.case_id]
        checks = row["source_checks"]
        locations = {s.evidence_id: s for s in case.source_locations}
        if (
            row["verdict"] not in {"ai_verified", "ai_verified_after_revision"}
            or row["review_method"] != "ai_original_pdf_source_review"
            or row["formal_human_approval"] is not False
            or row["reviewed_case_sha256"] != record_sha256(case)
            or set(row["checked_evidence_ids"]) != set(case.gold_evidence_ids)
            or len(checks) != len(case.gold_evidence_ids)
            or {c["evidence_id"] for c in checks} != set(case.gold_evidence_ids)
            or any(
                c["quote_verified_in_original_pdf_region"] is not True for c in checks
            )
            or any(
                c["evidence_id"] not in locations
                or (c["paper_id"], c["page"], c["pdf_sha256"])
                != (
                    locations[c["evidence_id"]].paper_id,
                    locations[c["evidence_id"]].pdf_page,
                    locations[c["evidence_id"]].source_document_sha256,
                )
                for c in checks
            )
            or (case.abstention and not row["boundary_scope_checked"])
        ):
            raise ValueError("Review proof invalid or stale: " + case.case_id)
        raw = case.model_dump(mode="json")
        raw.update(
            annotation_status="approved_ai_reference", split=splits[case.case_id]
        )
        raw["ai_review"] = {
            "reviewer": row["reviewer"],
            "reviewed_at": row["reviewed_at"],
            "method": row["review_method"],
            "verdict": "verified",
            "source_case_sha256": record_sha256(case),
            "review_record_sha256": canonical_hash(row),
            "case_content_sha256": reference_content_sha256(raw),
            "corpus_snapshot_id": dataset.manifest.corpus_snapshot_id,
            "checked_evidence_ids": row["checked_evidence_ids"],
            "checked_boundary_scope": row["boundary_scope_checked"],
            "notes": row["rationale_zh"],
        }
        cases.append(QualityCase.model_validate(raw))
    manifest = dataset.manifest.model_dump(mode="json")
    manifest.update(
        reference_kind="ai_reviewed",
        notes=[
            "90 approved AI-reviewed references; not an independently human-reviewed Gold Set.",
            "All AI approvals derive from existing original-PDF review records, with case-content and review-record hashes.",
            "Three wording corrections from the reviewed revision are retained; expected labels and original corpus are unchanged.",
            "18 dev / 72 test; shared fact-group connected components remain in the same split.",
            "Open-answer prediction semantics still require separate human assessments; reference approval does not grade predictions.",
            "Original F1 candidates, human Gold contract and Stage E corpus remain separate and preserved.",
        ],
    )
    return QualityDataset(
        manifest=QualityManifest.model_validate(manifest), cases=cases
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=SOURCE_DATASET)
    parser.add_argument("--reviews", type=Path, default=REVIEW_RECORDS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DATASET)
    args = parser.parse_args()
    source = load_dataset(args.dataset)
    rows = [
        json.loads(line)
        for line in args.reviews.read_text().splitlines()
        if line.strip()
    ]
    dataset = build_reference_dataset(source, rows)
    contents = {}
    manifest = dataset.manifest.model_dump(mode="json")
    for task, entry in manifest["case_files"].items():
        cases = [c for c in dataset.cases if c.task_type == task]
        content = "".join(c.model_dump_json() + "\n" for c in cases)
        contents[entry["filename"]] = content
        entry.update(
            count=len(cases), sha256=hashlib.sha256(content.encode()).hexdigest()
        )
    dataset = QualityDataset(
        manifest=QualityManifest.model_validate(manifest), cases=dataset.cases
    )
    records, snapshot = source_index(ROOT)
    validation = validate_dataset(dataset, records, snapshot)
    contents["manifest.json"] = dataset.manifest.model_dump_json(indent=2) + "\n"
    # 任何目标差异都先拒绝，不覆盖后续人工修订或旧版本。
    for name, content in contents.items():
        path = args.output_dir / name
        if path.exists() and path.read_text() != content:
            raise ValueError("Existing reference dataset differs: " + name)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        path = args.output_dir / name
        if not path.exists():
            path.write_text(content)
    print(
        json.dumps(
            validation
            | {"split_counts": dict(Counter(c.split for c in dataset.cases))},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
