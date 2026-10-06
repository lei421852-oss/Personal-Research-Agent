"""分别加载 dev/test，可显式要求数据已通过人工审阅。"""

from pathlib import Path
from typing import Literal

from eval.schemas import EvaluationDataset

DATASETS = Path(__file__).parent / "datasets"


class DatasetNotReviewedError(ValueError):
    pass


def load_dataset(
    split: Literal["dev", "test"],
    *,
    dataset_dir: Path = DATASETS,
    require_approved: bool = False,
) -> EvaluationDataset:
    if split not in {"dev", "test"}:
        raise ValueError("Split must be dev or test")
    dataset = EvaluationDataset.model_validate_json(
        (dataset_dir / f"{split}.json").read_text(encoding="utf-8")
    )
    if dataset.split != split:
        raise ValueError("Dataset split differs from its filename")
    if any(not case.case_id.startswith(f"{split}-") for case in dataset.cases):
        raise ValueError("Case ID split differs from dataset split")
    if require_approved:
        pending = [
            c.case_id for c in dataset.cases if c.annotation_status != "approved"
        ]
        if pending:
            raise DatasetNotReviewedError(
                f"{len(pending)} cases are not human-approved; candidate data is not Ground Truth"
            )
    return dataset


def load_datasets(
    *, dataset_dir: Path = DATASETS, require_approved: bool = False
) -> tuple[EvaluationDataset, EvaluationDataset]:
    return tuple(
        load_dataset(split, dataset_dir=dataset_dir, require_approved=require_approved)
        for split in ["dev", "test"]
    )
