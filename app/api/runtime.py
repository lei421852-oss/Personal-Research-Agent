"""只组合 API 所需资源，Agent 的动作选择仍交给现有运行时。"""

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
    def __init__(
        self,
        corpus,
        retrieval,
        client_factory,
        *,
        retrieval_mode="test",
        paper_retriever=None,
        paper_catalog=None,
    ):
        self.corpus, self.retrieval = corpus, retrieval
        self.client_factory, self.retrieval_mode = client_factory, retrieval_mode
        self.paper_retriever, self.paper_catalog = paper_retriever, paper_catalog

    def run(self, question):
        # 每个 HTTP 请求独享客户端、计数器、执行器和状态，不共享历史或会话。
        with ExitStack() as stack:
            llm, vision = self.client_factory(stack)
            counted = CountingVisionClient(vision)
            executor = ToolExecutor(
                create_paper_registry(
                    self.corpus,
                    self.retrieval,
                    analyzer=FigureAnalyzer(counted),
                    paper_retriever=self.paper_retriever,
                    paper_catalog=self.paper_catalog,
                )
            )
            result = BoundedToolAgent(
                llm,
                executor,
                EvidenceResolver(self.corpus, paper_catalog=self.paper_catalog),
            ).run(question)
            return map_agent_result(
                result,
                self.corpus,
                vlm_calls=counted.calls,
                retrieval_mode=self.retrieval_mode,
            )

    def close(self):
        pass  # 客户端已按请求关闭；语料库和检索器不持有模型提供方的会话。


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
    paper_catalog, paper_retriever = None, None
    if os.environ.get("AGENT_PAPER_DISCOVERY", "0") == "1":
        from app.papers.catalog import DEFAULT_FIXTURE, load_paper_catalog
        from app.papers.retriever import PaperRetriever
        from app.papers.store import DEFAULT_INDEX, ChromaPaperVectorStore
        from app.retrieval.embedding import QwenEmbeddingModel

        paper_catalog = load_paper_catalog()
        paper_model = os.environ.get(
            "AGENT_PAPER_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"
        )
        paper_store = ChromaPaperVectorStore(
            os.environ.get("AGENT_PAPER_INDEX_DIR", str(DEFAULT_INDEX))
        )
        paper_store.validate_provenance(DEFAULT_FIXTURE, paper_model)
        if mode == "real" and paper_model == os.environ.get(
            "AGENT_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"
        ):
            paper_embedding = embedding
        else:
            paper_embedding = QwenEmbeddingModel(
                paper_model, device=os.environ.get("AGENT_DEVICE", "cpu")
            )
        paper_retriever = PaperRetriever(paper_embedding, paper_store)
    # 固定本次配置快照；凭据不得进入 DTO、日志或对象的 repr 输出。
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

    return AgentAPIRuntime(
        corpus,
        retrieval,
        client_factory,
        retrieval_mode=mode,
        paper_retriever=paper_retriever,
        paper_catalog=paper_catalog,
    )
