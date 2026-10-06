"""API resource composition only; the existing Agent runs unchanged."""

import os
from contextlib import ExitStack
from pathlib import Path

from app.agent.loop import BoundedToolAgent
from app.api.mapping import map_agent_result
from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationUnavailable(ValueError):
    pass


class CountingVisionClient:
    def __init__(self, client):
        self.client, self.calls = client, 0

    def analyze(self, image_path, prompt):
        self.calls += 1
        return self.client.analyze(image_path, prompt)


class AgentAPIRuntime:
    def __init__(self, corpus, retrieval, client_factory, *, retrieval_mode="test"):
        self.corpus, self.retrieval = corpus, retrieval
        self.client_factory, self.retrieval_mode = client_factory, retrieval_mode

    def run(self, question):
        # Separate clients/counters/executor/state per HTTP request, no history or sessions.
        with ExitStack() as stack:
            llm, vision = self.client_factory(stack)
            counted = CountingVisionClient(vision)
            executor = ToolExecutor(
                create_paper_registry(
                    self.corpus, self.retrieval, analyzer=FigureAnalyzer(counted)
                )
            )
            result = BoundedToolAgent(llm, executor, EvidenceResolver(self.corpus)).run(
                question
            )
            return map_agent_result(
                result,
                self.corpus,
                vlm_calls=counted.calls,
                retrieval_mode=self.retrieval_mode,
            )

    def close(self):
        pass  # Clients are already closed per request; corpus/retriever contain no open provider sessions.


def create_configured_runtime():
    env_file = os.environ.get("AGENT_ENV_FILE")
    if env_file:
        from dotenv import load_dotenv

        path = Path(env_file)
        if not path.is_file():
            raise ConfigurationUnavailable("Configured environment file does not exist")
        load_dotenv(path, override=False)
    required = [
        f"{kind}_{field}"
        for kind in ["LLM", "VLM"]
        for field in ["API_KEY", "MODEL", "BASE_URL"]
    ]
    if any(not os.environ.get(k) for k in required):
        raise ConfigurationUnavailable("Model configuration is incomplete")
    corpus = load_paper_corpus(ROOT / "data/papers")
    mode = os.environ.get("AGENT_RETRIEVAL_BACKEND", "day5-controlled")
    if mode == "day5-controlled":
        from scripts.demo_day5 import ControlledRetrieval

        retrieval = ControlledRetrieval(corpus)
    elif mode == "real":
        from app.retrieval.embedding import QwenEmbeddingModel
        from app.retrieval.retriever import TextRetriever
        from app.retrieval.vector_store import ChromaTextVectorStore

        embedding = QwenEmbeddingModel(
            os.environ.get("AGENT_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
            device=os.environ.get("AGENT_DEVICE", "cpu"),
        )
        store = ChromaTextVectorStore(
            os.environ.get("AGENT_INDEX_DIR", str(ROOT / "data/chroma/day2")),
            "papers-day2-text",
        )
        retrieval = TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)
    else:
        raise ConfigurationUnavailable("Unknown retrieval backend")
    # Snapshot configuration, never include credentials in DTOs, logging or repr output.
    config = {k: os.environ[k] for k in required}

    def client_factory(stack):
        from app.llm.provider import OpenAICompatibleLLMClient
        from app.vision.provider import OpenAICompatibleVisionClient

        llm = OpenAICompatibleLLMClient(
            api_key=config["LLM_API_KEY"],
            model=config["LLM_MODEL"],
            base_url=config["LLM_BASE_URL"],
        )
        stack.callback(llm.close)
        vision = OpenAICompatibleVisionClient(
            api_key=config["VLM_API_KEY"],
            model=config["VLM_MODEL"],
            base_url=config["VLM_BASE_URL"],
        )
        stack.callback(vision.close)
        return llm, vision

    return AgentAPIRuntime(corpus, retrieval, client_factory, retrieval_mode=mode)
