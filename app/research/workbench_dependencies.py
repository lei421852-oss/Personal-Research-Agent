"""工作台只读预检：启动时不装配模型、Provider 或 Chroma 客户端。"""

import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from app.evidence.corpus import load_paper_corpus
from app.evidence.models import TextEvidence
from app.research.corpus_validation import corpus_snapshot, validate_index_records
from app.research.runtime import ResearchRuntimeConfig

ROOT = Path(__file__).resolve().parents[2]
LIMITS = {
    "max_concurrent_runs": 1,
    "max_papers": 3,
    "max_queued_runs": 2,
    "max_model_calls": 200,
    "max_tool_calls": 64,
    "timeout_seconds": 420,
    "history_retained_runs": 20,
    "history_ttl_seconds": 3600,
}


class WorkbenchUnavailable(ValueError):
    """只携带稳定错误代码；底层路径、地址和异常不能进入 API。"""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _cache_snapshot(model_name, environment):
    """只验证固定模型当前缓存，不请求远程 refs 或尝试下载。"""
    home = Path(environment.get("HF_HOME") or Path.home() / ".cache/huggingface")
    hub = Path(environment.get("HF_HUB_CACHE") or home / "hub")
    directory = hub / ("models--" + model_name.replace("/", "--"))
    reference = directory / "refs/main"
    if not reference.is_file():
        raise WorkbenchUnavailable("embedding_cache_unavailable")
    revision = reference.read_text(encoding="utf-8").strip()
    if not revision or Path(revision).name != revision:
        raise WorkbenchUnavailable("embedding_cache_unavailable")
    snapshot = directory / "snapshots" / revision
    required = ["config.json", "tokenizer.json", "tokenizer_config.json"]
    shard_index = snapshot / "model.safetensors.index.json"
    if shard_index.is_file():
        mapping = json.loads(shard_index.read_text(encoding="utf-8"))["weight_map"]
        required.extend(set(mapping.values()))
    else:
        required.append("model.safetensors")
    if any(
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not (snapshot / name).is_file()
        or (snapshot / name).stat().st_size == 0
        for name in required
    ):
        raise WorkbenchUnavailable("embedding_cache_unavailable")
    return snapshot


def _read_index(config, root, corpus, snapshot):
    """查询既有 SQLite，只读验证 collection 绑定和所有证据记录。"""
    directory = root / config["index_dir"]
    database = directory / "chroma.sqlite3"
    if not database.is_file():
        raise WorkbenchUnavailable("index_unavailable")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only = ON")
        collection = db.execute(
            "SELECT id, dimension, schema_str FROM collections WHERE name = ?",
            (config["collection"],),
        ).fetchone()
        if collection is None or not collection[1]:
            raise WorkbenchUnavailable("index_unavailable")
        schema = json.loads(collection[2])
        space = schema["keys"]["#embedding"]["float_list"]["vector_index"]["config"][
            "space"
        ]
        metadata = {
            key: next(value for value in values if value is not None)
            for key, *values in db.execute(
                "SELECT key, str_value, int_value, float_value, bool_value "
                "FROM collection_metadata WHERE collection_id = ?",
                (collection[0],),
            )
        }
        expected = {
            "corpus_snapshot_id": snapshot["snapshot_id"],
            "embedding_model": config["embedding_model"],
            "distance": config["distance"],
            "schema_version": 1,
        }
        if metadata != expected or space != "cosine":
            raise WorkbenchUnavailable("index_binding_mismatch")
        segments = db.execute(
            "SELECT id, scope FROM segments WHERE collection = ?", (collection[0],)
        ).fetchall()
        vectors = [identity for identity, scope in segments if scope == "VECTOR"]
        if len(vectors) != 1 or not all(
            (directory / vectors[0] / name).is_file()
            for name in (
                "header.bin",
                "data_level0.bin",
                "length.bin",
                "link_lists.bin",
            )
        ):
            raise WorkbenchUnavailable("index_unavailable")
        rows = db.execute(
            "SELECT e.id, e.embedding_id, m.key, m.string_value, m.int_value, "
            "m.float_value, m.bool_value FROM embeddings e "
            "JOIN segments s ON e.segment_id = s.id "
            "LEFT JOIN embedding_metadata m ON e.id = m.id "
            "WHERE s.collection = ? AND s.scope = 'METADATA'",
            (collection[0],),
        ).fetchall()
    records = {}
    for identity, evidence_id, key, *values in rows:
        record = records.setdefault(identity, {"id": evidence_id, "fields": {}})
        if key is not None:
            record["fields"][key] = next(v for v in values if v is not None)
    projected = []
    for record in records.values():
        fields = record["fields"]
        text = fields.pop("chroma:document", None)
        if record["id"] != fields.get("evidence_id") or text is None:
            raise WorkbenchUnavailable("index_binding_mismatch")
        projected.append(TextEvidence(**fields, text=text))
    validate_index_records(corpus, projected)
    return {"record_count": len(projected), "embedding_dimension": collection[1]}


class WorkbenchDependencies:
    """固定管理员配置与只读 Corpus；公开健康结果不带敏感配置。"""

    def __init__(self, root=ROOT, env_file=None):
        self.root = Path(root)
        self.env_file = Path(env_file) if env_file else self.root / ".env"

    def environment(self):
        from dotenv import dotenv_values

        # 用独立字典保留环境变量优先级，不修改全局 os.environ。
        file_values = (
            dotenv_values(self.env_file, interpolate=False)
            if self.env_file.is_file()
            else {}
        )
        return {**file_values, **os.environ}

    def config(self):
        config = json.loads(
            (self.root / "configs/cross_paper_eval.json").read_text(encoding="utf-8")
        )
        ResearchRuntimeConfig(**config["runtime"])
        for field in ("papers_dir", "additional_papers_dir", "index_dir"):
            path = Path(config[field])
            if path.is_absolute() or not (self.root / path).resolve().is_relative_to(
                self.root.resolve()
            ):
                raise WorkbenchUnavailable("configuration_invalid")
        return config

    def corpus(self, config=None):
        config = config or self.config()
        corpus = load_paper_corpus(
            self.root / config["papers_dir"],
            additional_papers_dir=self.root / config["additional_papers_dir"],
        )
        snapshot = corpus_snapshot(
            self.root / config["papers_dir"],
            corpus,
            self.root / config["additional_papers_dir"],
        )
        if snapshot["snapshot_id"] != config["corpus_snapshot_id"]:
            raise WorkbenchUnavailable("corpus_binding_mismatch")
        return corpus, snapshot

    def papers(self):
        corpus, _ = self.corpus()
        texts = Counter(item.paper_id for item in corpus.text_evidence)
        figures = Counter(item.paper_id for item in corpus.figure_evidence)
        return [
            {
                "paper_id": item.paper_id,
                "title": item.title,
                "text_evidence_count": texts[item.paper_id],
                "figure_evidence_count": figures[item.paper_id],
            }
            for item in corpus.metadata
            if texts[item.paper_id]
        ]

    def provider_settings(self):
        environment = self.environment()
        for kind in ("LLM", "VLM"):
            if any(
                not (environment.get(f"{kind}_{field}") or "").strip()
                for field in ("API_KEY", "MODEL", "BASE_URL")
            ):
                raise WorkbenchUnavailable("provider_configuration_incomplete")
            address = urlparse(environment[f"{kind}_BASE_URL"])
            if address.scheme not in {"https", "http"} or not address.hostname:
                raise WorkbenchUnavailable("provider_configuration_invalid")
        return {
            kind: {
                field: environment[f"{kind}_{field}"]
                for field in ("API_KEY", "MODEL", "BASE_URL")
            }
            for kind in ("LLM", "VLM")
        }

    def health(self):
        dependencies = []
        ui_ready = all(
            (self.root / "app/workbench_web" / name).is_file()
            for name in ("index.html", "style.css", "app.js")
        )
        dependencies.append(
            {
                "name": "ui_assets",
                "status": "ready" if ui_ready else "blocked",
                "message": "工作台静态资源完整。"
                if ui_ready
                else "工作台静态资源缺失。",
            }
        )

        def check(name, operation, ready, blocked):
            try:
                result = operation()
                dependencies.append({"name": name, "status": "ready", "message": ready})
                return result
            except Exception:  # noqa: BLE001 - 公开枚举替代路径和原始异常
                dependencies.append(
                    {"name": name, "status": "blocked", "message": blocked}
                )
                return None

        config = check(
            "configuration", self.config, "固定研究配置已加载。", "固定研究配置不可用。"
        )
        prepared = (
            check(
                "prepared_corpus",
                lambda: self.corpus(config),
                "预处理论文证据及来源校验通过。",
                "预处理语料缺失或来源绑定不一致。",
            )
            if config
            else None
        )
        check(
            "provider",
            self.provider_settings,
            "模型服务配置完整；尚未发送请求。",
            "模型服务配置不完整；仍可使用历史回放。",
        )
        if config:
            check(
                "embedding_cache",
                lambda: _cache_snapshot(config["embedding_model"], self.environment()),
                "本地模型缓存存在；尚未加载模型或执行 Embedding。",
                "本地模型缓存不完整；不会自动下载。",
            )
        if prepared:
            check(
                "evidence_index",
                lambda: _read_index(config, self.root, *prepared),
                "既有索引与全部 Prepared Evidence 绑定一致。",
                "既有索引缺失、不完整或绑定不一致；不会自动重建。",
            )
        replay = all(
            (self.root / path).is_file()
            for path in (
                "docs/demos/blip2-vs-flamingo/execution-record.json",
                "docs/acceptance/stage_g2_1/results/execution-record.json",
            )
        )
        return {
            "ui_ready": ui_ready,
            "replay_ready": replay,
            "real_research_ready": bool(prepared)
            and all(item["status"] == "ready" for item in dependencies),
            "dependencies": dependencies,
            "limits": LIMITS.copy(),
        }

    def prepare_resources(self):
        """仅创建真实任务后装配模型；所有 from_pretrained 都显式仅用本地文件。"""
        config = self.config()
        corpus, snapshot = self.corpus(config)
        self.provider_settings()
        _read_index(config, self.root, corpus, snapshot)
        cache = _cache_snapshot(config["embedding_model"], self.environment())
        import torch
        from transformers import AutoModel, AutoTokenizer

        from app.retrieval.embedding import QwenEmbeddingModel
        from app.retrieval.retriever import TextRetriever
        from app.retrieval.vector_store import ChromaTextVectorStore

        # 复用原 Embedding 方法，仅补充离线构造所需的组件和同名属性。
        embedding = QwenEmbeddingModel.__new__(QwenEmbeddingModel)
        embedding.torch = torch
        embedding.batch_size = 8
        embedding.tokenizer = AutoTokenizer.from_pretrained(
            str(cache), local_files_only=True, trust_remote_code=False
        )
        embedding.model = (
            AutoModel.from_pretrained(
                str(cache), local_files_only=True, trust_remote_code=False
            )
            .to(config["device"])
            .eval()
        )
        store = ChromaTextVectorStore(
            self.root / config["index_dir"], config["collection"], create=False
        )
        expected = {
            "corpus_snapshot_id": snapshot["snapshot_id"],
            "embedding_model": config["embedding_model"],
            "distance": config["distance"],
            "schema_version": 1,
        }
        if store.collection.metadata != expected:
            raise WorkbenchUnavailable("index_binding_mismatch")
        records = store.collection.get(include=["documents", "metadatas"])
        validate_index_records(
            corpus,
            [
                TextEvidence(**metadata, text=text)
                for metadata, text in zip(
                    records["metadatas"], records["documents"], strict=True
                )
            ],
        )
        retriever = TextRetriever(embedding, store, paper_aliases=corpus.paper_aliases)
        return corpus, snapshot, config, retriever
