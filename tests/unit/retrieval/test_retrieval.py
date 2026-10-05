import pytest

from app.evidence.models import TextEvidence
from app.retrieval.retriever import TextRetriever
from app.retrieval.vector_store import ChromaTextVectorStore


class FakeEmbedding:
    def __init__(self):
        self.queries = []

    def embed_query(self, query):
        self.queries.append(query)
        return [1.0, 0.0]


class FakeVectorStore:
    def search(self, query_embedding, *, paper_ids, top_k):
        self.received = (query_embedding, paper_ids, top_k)
        return []


def test_filter_is_forwarded_to_store_not_postfiltered():
    embedding, store = FakeEmbedding(), FakeVectorStore()
    retriever = TextRetriever(embedding, store)
    assert retriever.search_text(" CapFilt? ", ["blip-v2"], 5) == []
    assert store.received == ([1.0, 0.0], ["blip-v2"], 5)
    retriever.search_text("question", None, 2)
    assert store.received[1] is None
    assert embedding.queries == ["CapFilt?", "question"]


@pytest.mark.parametrize(
    "query,top_k,papers",
    [
        (" ", 3, None),
        ("x", 0, None),
        ("x", -1, None),
        ("x", True, None),
        ("x", 3, [""]),
        ("x", 3, "blip"),
    ],
)
def test_invalid_requests_rejected_before_embedding(query, top_k, papers):
    embedding = FakeEmbedding()
    with pytest.raises(ValueError):
        TextRetriever(embedding, FakeVectorStore()).search_text(query, papers, top_k)
    assert embedding.queries == []


def test_empty_paper_selection_never_searches_everything():
    embedding = FakeEmbedding()
    assert TextRetriever(embedding, FakeVectorStore()).search_text("x", [], 3) == []
    assert embedding.queries == []


class FakeCollection:
    def __init__(self):
        self.configuration = {"hnsw": {"space": "cosine"}}

    def upsert(self, **kwargs):
        self.records = kwargs

    def count(self):
        return len(self.records["ids"])

    def query(self, **kwargs):
        self.query_args = kwargs
        return {
            key: [self.records[key]] for key in ["ids", "documents", "metadatas"]
        } | {"distances": [[0.2]]}


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()

    def get_collection(self, name):
        return self.collection


def test_metadata_roundtrip_and_where_are_preserved():
    client = FakeClient()
    store = ChromaTextVectorStore("unused", "test", client=client)
    original = TextEvidence(
        evidence_id="ev1", paper_id="blip-v2", page=3, text="Captioner"
    )
    store.add([original], [[1.0, 0.0]])
    hit = store.search([1.0, 0.0], paper_ids=["blip-v2"], top_k=3)[0]
    assert hit.evidence == original
    assert hit.retrieval_score == pytest.approx(0.8)
    assert client.collection.query_args["where"] == {
        "$and": [{"modality": "text"}, {"paper_id": {"$in": ["blip-v2"]}}]
    }
    assert "retrieval_score" not in client.collection.records["metadatas"][0]
