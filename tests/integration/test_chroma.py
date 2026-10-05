"""Local database test only: no model, network, or downloaded weights."""

import pytest

pytest.importorskip("chromadb")

from app.evidence.models import TextEvidence
from app.retrieval.vector_store import ChromaTextVectorStore


def test_persistent_roundtrip_filters_before_top_k(tmp_path):
    store = ChromaTextVectorStore(tmp_path, "evidence-test", create=True)
    blip = TextEvidence(evidence_id="ev-blip", paper_id="blip-v2", page=3, text="CapFilt")
    other = TextEvidence(evidence_id="ev-other", paper_id="other", page=1, text="Other")
    store.add([blip, other], [[0.8, 0.2], [1.0, 0.0]])
    reopened = ChromaTextVectorStore(tmp_path, "evidence-test")
    assert reopened.search([1.0, 0.0], paper_ids=None, top_k=1)[0].evidence == other
    assert reopened.search([1.0, 0.0], paper_ids=["blip-v2"], top_k=1)[0].evidence == blip
    assert reopened.search([1.0, 0.0], paper_ids=["missing"], top_k=3) == []
    reopened.add([blip], [[0.8, 0.2]])
    assert reopened.collection.count() == 2
