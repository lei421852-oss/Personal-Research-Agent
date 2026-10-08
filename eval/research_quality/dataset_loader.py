"""按清单校验字节哈希再解析数据；Gold 选择与候选加载分离。"""

import hashlib
from pathlib import Path

from eval.research_quality.schemas import QualityCase, QualityDataset, QualityManifest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "data/benchmark/research_quality_v1"


def load_jsonl(path, model):
    return [
        model.model_validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]


def load_dataset(directory: Path = DEFAULT_DATASET) -> QualityDataset:
    directory = directory.resolve()
    manifest = QualityManifest.model_validate_json(
        (directory / "manifest.json").read_text()
    )
    cases = []
    for task_type, entry in manifest.case_files.items():
        path = (directory / entry.filename).resolve()
        if not path.is_relative_to(directory) or Path(entry.filename).is_absolute():
            raise ValueError("Benchmark file escapes dataset directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.sha256:
            raise ValueError("Benchmark file hash mismatch: " + entry.filename)
        rows = load_jsonl(path, QualityCase)
        if len(rows) != entry.count or any(c.task_type != task_type for c in rows):
            raise ValueError("Manifest count/task type differs from case file")
        cases.extend(rows)
    return QualityDataset(manifest=manifest, cases=cases)


def approved_gold(dataset, *, split="test"):
    if split not in {"dev", "test", "all"}:
        raise ValueError("Gold selection requires dev, test or all")
    dataset = QualityDataset.model_validate(dataset.model_dump(mode="python"))
    return [
        c
        for c in dataset.cases
        if c.annotation_status == "approved_gold"
        and (split == "all" or c.split == split)
    ]


def evaluation_input(case):
    """未来执行器只取得问题与待审材料，不获得参考答案或预期标签。"""
    payload = {
        "case_id": case.case_id,
        "task_type": case.task_type,
        "paper_ids": case.paper_ids,
        "dimension": case.dimension,
        "question": case.question,
    }
    if case.reviewer:
        payload["review_target"] = {
            "claim": case.reviewer.claim,
            "cited_evidence_ids": case.reviewer.cited_evidence_ids,
        }
        payload["provided_evidence"] = [
            s.model_dump(mode="json")
            for s in case.source_locations
            if s.evidence_id in case.reviewer.cited_evidence_ids
        ]
    elif case.abstention:
        payload["assessment_scope"] = case.abstention.assessment_scope
        if case.abstention.assessment_scope == "provided_caption_only":
            payload["provided_evidence"] = [
                s.model_dump(mode="json") for s in case.source_locations
            ]
    return payload
