"""只读现有来源；测试中的人工身份是合成 fixture，不写入候选数据。"""

import pytest

from eval.research_quality.dataset_loader import ROOT, load_dataset
from eval.research_quality.dataset_validator import source_index


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


@pytest.fixture(scope="module")
def sources():
    return source_index(ROOT)
