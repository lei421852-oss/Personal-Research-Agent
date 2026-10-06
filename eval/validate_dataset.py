"""Static corpus/annotation checks only. No inference, grading, or Agent changes."""

import argparse
import json
from collections import Counter
from pathlib import Path

from app.evidence.corpus import PaperCorpus, load_paper_corpus
from app.evidence.models import FigureEvidence
from eval.loader import DATASETS, load_datasets
from eval.schemas import EvaluationDataset

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"text_qa": 10, "figure_qa": 12, "cross_paper": 10, "insufficient": 8}


class DatasetValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("\n".join(issues))


def validate_datasets(
    dev: EvaluationDataset, test: EvaluationDataset, corpus: PaperCorpus
) -> dict:
    issues = []
    cases = [*dev.cases, *test.cases]
    if dev.split != "dev" or test.split != "test":
        issues.append("Expected distinct dev/test splits")
    if len(dev.cases) != 10 or len(test.cases) != 30 or len(cases) != 40:
        issues.append("Require exactly 40 cases: 10 dev and 30 test")
    counts = Counter(c.category for c in cases)
    if counts != EXPECTED:
        issues.append(f"Category counts must be {EXPECTED}; got {dict(counts)}")
    ids = [c.case_id for c in cases]
    if len(ids) != len(set(ids)):
        issues.append("Duplicate case_id across dev/test")
    questions = [" ".join(c.question.split()).casefold() for c in cases]
    if len(questions) != len(set(questions)):
        issues.append("Duplicate question across dev/test")
    evidence = {
        e.evidence_id: e for e in (*corpus.text_evidence, *corpus.figure_evidence)
    }
    papers = {m.paper_id: m for m in corpus.metadata}
    figures = {(e.paper_id, e.figure_id): e for e in corpus.figure_evidence}
    for dataset in [dev, test]:
        if any(not c.case_id.startswith(dataset.split + "-") for c in dataset.cases):
            issues.append(f"{dataset.split}: case ID split mismatch")
        snapshot = {s.paper_id: s for s in dataset.corpus_snapshot}
        if len(snapshot) != len(dataset.corpus_snapshot) or set(snapshot) != set(
            papers
        ):
            issues.append(f"{dataset.split}: corpus snapshot paper set differs")
        for paper_id, snap in snapshot.items():
            actual = papers.get(paper_id)
            if actual is None:
                continue
            if snap.pdf_sha256 != actual.pdf_sha256:
                issues.append(
                    f"{dataset.split}: PDF version/hash mismatch for {paper_id}"
                )
            if sorted(snap.prepared_pages) != sorted(p.page for p in actual.pages):
                issues.append(
                    f"{dataset.split}: prepared page snapshot mismatch for {paper_id}"
                )
            if sorted(snap.prepared_figure_ids) != sorted(
                f.figure_id for f in actual.figures
            ):
                issues.append(
                    f"{dataset.split}: prepared figure snapshot mismatch for {paper_id}"
                )
    for case in cases:

        def fail(message, case_id=case.case_id):
            issues.append(f"{case_id}: {message}")

        if any(p not in papers for p in case.paper_ids):
            fail("Unknown/noncanonical paper_id")
        present = []
        for ref in case.evidence_references:
            item = evidence.get(ref.evidence_id)
            if item is None:
                fail(f"Unknown evidence_id {ref.evidence_id}")
                continue
            present.append(item)
            if ref.paper_id not in case.paper_ids:
                fail("Evidence belongs to a paper outside case scope")
            if ref.paper_id != item.paper_id or ref.page != item.page:
                fail("Evidence paper/page provenance mismatch")
            if ref.figure_id != (
                item.figure_id if isinstance(item, FigureEvidence) else None
            ):
                fail("Figure ID or evidence modality mismatch")
        for target in case.target_figures:
            item = figures.get((target.paper_id, target.figure_id))
            if (
                item is None
                or item.page != target.page
                or target.paper_id not in case.paper_ids
            ):
                fail("Target figure is missing or page/scope is invalid")
        if case.category == "figure_qa" and not any(
            isinstance(e, FigureEvidence) for e in present
        ):
            fail("Figure QA requires actual Figure Evidence")
        if case.category == "cross_paper" and len({e.paper_id for e in present}) < 2:
            fail("Cross-paper QA requires evidence from at least two papers")
    if issues:
        raise DatasetValidationError(issues)
    reviewed = Counter(c.annotation_status for c in cases)
    ready = reviewed.get("approved", 0) == 40
    return {
        "static_valid": True,
        "total_cases": 40,
        "splits": {"dev": len(dev.cases), "test": len(test.cases)},
        "categories": dict(counts),
        "review_status": dict(reviewed),
        "ground_truth_ready": ready,
        "regression_cases": [c.case_id for c in cases if c.regression],
        "corpus": {
            "papers": len(papers),
            "text_evidence": len(corpus.text_evidence),
            "figure_evidence": len(corpus.figure_evidence),
        },
        "notes": [
            "Static checks validate structure and provenance, not semantic support or human approval.",
            "Twelve Figure QA cases reuse three figures; they are not twelve independent images.",
            "No formal evaluation or model request was executed.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DATASETS)
    parser.add_argument("--papers-dir", type=Path, default=ROOT / "data/papers")
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        dev, test = load_datasets(dataset_dir=args.dataset_dir)
        report = validate_datasets(dev, test, load_paper_corpus(args.papers_dir))
    except (ValueError, OSError) as exc:
        print(f"Static dataset validation failed:\n{exc}")
        raise SystemExit(1) from None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.require_reviewed and not report["ground_truth_ready"]:
        raise SystemExit(
            "Human review is incomplete; candidates cannot be used as approved Ground Truth."
        )


if __name__ == "__main__":
    main()
