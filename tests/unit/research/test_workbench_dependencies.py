"""工作台预检只能读取现有数据，不能加载模型、创建索引或泄露配置。"""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from app.research.workbench_dependencies import (
    ROOT,
    WorkbenchDependencies,
    WorkbenchUnavailable,
    _cache_snapshot,
    _read_index,
)
from scripts.launch import main


def test_prepared_paper_list_is_five_real_corpus_papers():
    papers = WorkbenchDependencies().papers()
    assert len(papers) == 5
    assert sum(p["text_evidence_count"] for p in papers) == 46
    assert sum(p["figure_evidence_count"] for p in papers) == 5
    assert {p["paper_id"] for p in papers} >= {
        "blip-2-2301.12597v1",
        "flamingo-2204.14198v1",
    }
    assert all(
        set(p) == {"paper_id", "title", "text_evidence_count", "figure_evidence_count"}
        for p in papers
    )


def test_health_without_keys_keeps_ui_and_replay_ready(monkeypatch):
    dependency = WorkbenchDependencies()
    monkeypatch.setattr(dependency, "environment", dict)
    before = os.environ.copy()
    health = dependency.health()
    assert health["ui_ready"] and health["replay_ready"]
    assert not health["real_research_ready"]
    assert (
        next(d for d in health["dependencies"] if d["name"] == "provider")["status"]
        == "blocked"
    )
    assert os.environ == before
    serialized = json.dumps(health)
    assert "api.deepseek.com" not in serialized
    assert str(ROOT) not in serialized
    assert "Traceback" not in serialized


def test_health_does_not_import_model_chroma_or_openai():
    script = """
import sys, json
from app.research.workbench_dependencies import WorkbenchDependencies
result = WorkbenchDependencies().health()
assert result['ui_ready']
assert not any(name in sys.modules for name in ('torch', 'transformers', 'chromadb', 'openai'))
print('read_only_preflight_passed')
"""
    checked = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert "read_only_preflight_passed" in checked.stdout


def test_existing_sqlite_index_read_is_content_preserving():
    dependencies = WorkbenchDependencies()
    config = dependencies.config()
    corpus, snapshot = dependencies.corpus(config)
    database = ROOT / config["index_dir"] / "chroma.sqlite3"
    if not database.exists():
        pytest.skip("Fresh checkout has no private local Chroma index")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    result = _read_index(config, ROOT, corpus, snapshot)
    assert result["record_count"] == 46
    assert result["embedding_dimension"] > 0
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest


def test_missing_index_does_not_create_any_file(tmp_path):
    dependencies = WorkbenchDependencies()
    config = dependencies.config() | {"index_dir": "missing"}
    corpus, snapshot = dependencies.corpus(dependencies.config())
    with pytest.raises(WorkbenchUnavailable, match="index_unavailable"):
        _read_index(config, tmp_path, corpus, snapshot)
    assert list(tmp_path.iterdir()) == []


def test_cache_requires_all_local_shards(tmp_path):
    model = "test/model"
    directory = tmp_path / "hub/models--test--model"
    (directory / "refs").mkdir(parents=True)
    (directory / "refs/main").write_text("revision")
    snapshot = directory / "snapshots/revision"
    snapshot.mkdir(parents=True)
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (snapshot / name).write_text("{}")
    (snapshot / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"a": "one.safetensors", "b": "two.safetensors"}})
    )
    (snapshot / "one.safetensors").write_bytes(b"test")
    with pytest.raises(WorkbenchUnavailable):
        _cache_snapshot(model, {"HF_HOME": str(tmp_path)})
    (snapshot / "two.safetensors").write_bytes(b"test")
    assert _cache_snapshot(model, {"HF_HOME": str(tmp_path)}) == snapshot


def test_environment_settings_do_not_mutate_process(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    filename = tmp_path / ".env"
    filename.write_text("LLM_API_KEY=test-secret\nHF_HUB_OFFLINE=1\n")
    before = os.environ.copy()
    environment = WorkbenchDependencies(env_file=filename).environment()
    assert environment["LLM_API_KEY"] == "test-secret"
    assert os.environ == before


def test_missing_config_health_is_sanitized(tmp_path):
    health = WorkbenchDependencies(root=tmp_path).health()
    assert not health["real_research_ready"]
    assert not health["ui_ready"]
    assert str(tmp_path) not in json.dumps(health)


def test_launch_check_uses_health_without_server(monkeypatch, capsys):
    monkeypatch.setattr(WorkbenchDependencies, "health", lambda _: {"ui_ready": True})
    assert main(["--check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ui_ready": True}


@pytest.mark.parametrize("arguments", [["--host", "0.0.0.0"], ["--port", "0"]])
def test_launch_rejects_nonlocal_host_and_invalid_port(arguments):
    with pytest.raises(SystemExit):
        main(arguments)


def test_config_rejects_path_escape(tmp_path):
    directory = tmp_path / "configs"
    directory.mkdir()
    config = json.loads((ROOT / "configs/cross_paper_eval.json").read_text())
    config["index_dir"] = "../outside"
    (directory / "cross_paper_eval.json").write_text(json.dumps(config))
    with pytest.raises(WorkbenchUnavailable, match="configuration_invalid"):
        WorkbenchDependencies(tmp_path).config()


def test_prepare_resources_uses_only_existing_local_model(monkeypatch):
    """只装配 Fake 组件确认 local_files_only，不实际加载 1.2GB 模型。"""
    from types import ModuleType, SimpleNamespace

    dependency = WorkbenchDependencies()
    config = dependency.config()
    corpus, snapshot = dependency.corpus(config)
    calls = []

    class LocalLoader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            calls.append((path, kwargs))
            return LocalLoader()

        def to(self, device):
            return self

        def eval(self):
            return self

    module = ModuleType("transformers")
    module.AutoModel = LocalLoader
    module.AutoTokenizer = LocalLoader
    monkeypatch.setitem(sys.modules, "transformers", module)
    monkeypatch.setitem(sys.modules, "torch", ModuleType("torch"))
    monkeypatch.setattr(dependency, "provider_settings", dict)
    monkeypatch.setattr(
        "app.research.workbench_dependencies._cache_snapshot",
        lambda *_: ROOT / "cached",
    )
    monkeypatch.setattr(
        "app.research.workbench_dependencies._read_index", lambda *_: {}
    )

    class Store:
        def __init__(self, *args, **kwargs):
            assert kwargs == {"create": False}
            self.collection = SimpleNamespace(
                metadata={
                    "corpus_snapshot_id": snapshot["snapshot_id"],
                    "embedding_model": config["embedding_model"],
                    "distance": "cosine",
                    "schema_version": 1,
                },
                get=lambda **_: {
                    "documents": [item.text for item in corpus.text_evidence],
                    "metadatas": [
                        item.model_dump(exclude={"text"})
                        for item in corpus.text_evidence
                    ],
                },
            )

    monkeypatch.setattr("app.retrieval.vector_store.ChromaTextVectorStore", Store)
    prepared = dependency.prepare_resources()
    assert len(prepared[0].metadata) == 5
    assert len(calls) == 2
    assert all(
        kwargs == {"local_files_only": True, "trust_remote_code": False}
        for _, kwargs in calls
    )
    assert (
        "HF_HUB_OFFLINE" not in os.environ
        or os.environ["HF_HUB_OFFLINE"] != "modified-by-workbench"
    )
