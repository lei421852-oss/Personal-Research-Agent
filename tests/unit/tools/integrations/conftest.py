"""本地子进程夹具；退出时确认会话清理，不依赖任何外部服务。"""

import sys
from pathlib import Path

import pytest

from app.tools.integrations.adapter import LocalStdioSource
from app.tools.integrations.models import ExternalServiceConfig
from app.tools.registry import ToolRegistry

MOCK = Path(__file__).with_name("mock_service.py")


@pytest.fixture
def integration(tmp_path):
    sessions = []

    def create(mode="normal", *, register=True, **settings):
        config = ExternalServiceConfig(
            provider="fixture",
            enabled=settings.pop("enabled", True),
            allowed_tools=settings.pop("allowed_tools", ("echo", "count")),
            **settings,
        )
        log = tmp_path / f"calls-{len(sessions)}.jsonl"
        source = LocalStdioSource(config, (sys.executable, str(MOCK), mode, str(log)))
        session = source.open()
        sessions.append(session)
        registry = ToolRegistry()
        if register:
            session.register(registry)
        return session, registry, log, source

    yield create
    for session in sessions:
        session.close()
        assert not session.client.active_pids
