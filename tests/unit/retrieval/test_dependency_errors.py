import pytest

from app.retrieval.errors import RetrievalDependencyError
from app.retrieval.retriever import TextRetriever
from app.retrieval.vector_store import ChromaTextVectorStore


class FailingEmbedding:
    def embed_query(self, query):
        raise RuntimeError("private provider detail")


def test_embedding_exception_is_an_explicit_dependency_failure():
    with pytest.raises(RetrievalDependencyError) as error:
        TextRetriever(FailingEmbedding(), object()).search_text("question")
    assert "private" not in str(error.value)
    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.parametrize("operation", ["count", "query"])
def test_database_exceptions_have_dependency_classification(operation):
    class FailingCollection:
        def __init__(self):
            self.configuration = {"hnsw": {"space": "cosine"}}

        def count(self):
            if operation == "count":
                raise RuntimeError("Chroma /home/lei secret")
            return 1

        def query(self, **kwargs):
            raise RuntimeError("Chroma /home/lei secret")

    class Client:
        def get_collection(self, name):
            return FailingCollection()

    store = ChromaTextVectorStore("unused", "test", client=Client())
    with pytest.raises(RetrievalDependencyError) as error:
        store.search([1.0], paper_ids=None, top_k=1)
    assert "Chroma" not in str(error.value)
