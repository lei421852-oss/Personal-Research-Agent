"""只记录操作事实，不建立网络或数据库连接。"""

import asyncio


class FakeResult:
    def scalar_one(self):
        return 1


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        if self.engine.connect_error:
            raise self.engine.connect_error
        self.engine.opened += 1
        return self

    async def exec_driver_sql(self, statement):
        self.engine.statements.append(statement)
        self.engine.query_started.set()
        if self.engine.execute_error:
            raise self.engine.execute_error
        if self.engine.query_release:
            await self.engine.query_release.wait()
        return FakeResult()

    async def __aexit__(self, *args):
        self.engine.released += 1


class FakeEngine:
    def __init__(self):
        self.opened = self.released = self.disposed = 0
        self.statements = []
        self.connect_error = self.execute_error = self.dispose_error = None
        self.query_started = asyncio.Event()
        self.query_release = None

    def connect(self):
        return FakeConnection(self)

    async def dispose(self):
        self.disposed += 1
        if self.dispose_error:
            raise self.dispose_error


class FakeSession:
    def __init__(self):
        self.rollbacks = self.closed = self.commits = 0
        self.rollback_error = self.close_error = None
        self.close_started = asyncio.Event()
        self.close_release = None

    async def rollback(self):
        self.rollbacks += 1
        if self.rollback_error:
            raise self.rollback_error

    async def close(self):
        self.close_started.set()
        if self.close_release:
            await self.close_release.wait()
        self.closed += 1
        if self.close_error:
            raise self.close_error


class Factories:
    def __init__(self):
        self.engine = FakeEngine()
        self.engine_calls = []
        self.session_options = None
        self.sessions = []

    def create_engine(self, url, **kwargs):
        self.engine_calls.append((url, kwargs))
        return self.engine

    def make_sessions(self, engine, **kwargs):
        self.session_options = kwargs

        def create():
            session = FakeSession()
            self.sessions.append(session)
            return session

        return create


class FakeManager:
    def __init__(self):
        self.closed = 0
        self.close_error = None

    async def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error
