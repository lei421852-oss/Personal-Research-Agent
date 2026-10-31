"""应用级Engine与短Session作用域；不定义业务表或自动提交。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.persistence.errors import DatabaseConfigurationError, StorageError

LOGGER = logging.getLogger(__name__)
CLEANUP_TIMEOUT_SECONDS = 5.0


@dataclass(eq=False)
class _Lease:
    session: object
    cleanup_task: asyncio.Task | None = None


def _consume(task):
    if not task.cancelled():
        task.exception()


async def _wait_cleanup(task):
    """取消请求不能直接取消清理；二次等待也受固定预算限制。"""
    deadline = asyncio.get_running_loop().time() + CLEANUP_TIMEOUT_SECONDS
    try:
        await asyncio.wait_for(asyncio.shield(task), CLEANUP_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        if task.cancelled() and not asyncio.current_task().cancelling():
            raise StorageError("storage_cleanup_failed") from None
        try:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            await asyncio.wait_for(asyncio.shield(task), remaining)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 - 取消不能中止清理收尾
            task.cancel()
            task.add_done_callback(_consume)
        raise
    except TimeoutError:
        task.cancel()
        task.add_done_callback(_consume)
        raise StorageError("storage_cleanup_failed") from None


class DatabaseResource:
    def __init__(self, settings, *, engine_factory=None, session_factory_builder=None):
        self.settings = settings
        self.engine = None
        self.session_factory = None
        self.state = "new"
        self._engine_factory = engine_factory
        self._session_factory_builder = session_factory_builder
        self._leases = set()
        self._close_task = None

    def __repr__(self):
        return f"DatabaseResource(state={self.state!r})"

    async def start(self):
        if self.state in {"initialized", "disabled"}:
            return
        if self.state != "new":
            raise StorageError("storage_closing")
        if not self.settings.enabled:
            self.state = "disabled"
            return
        url, connect_args = self.settings.connection_options()
        try:
            from sqlalchemy.ext.asyncio import (
                AsyncSession,
                async_sessionmaker,
                create_async_engine,
            )

            engine_factory = self._engine_factory or create_async_engine
            session_builder = self._session_factory_builder or async_sessionmaker
            self.engine = engine_factory(
                url,
                pool_size=self.settings.pool_size,
                max_overflow=self.settings.max_overflow,
                pool_timeout=self.settings.pool_timeout,
                pool_pre_ping=True,
                pool_recycle=1800,
                echo=False,
                echo_pool=False,
                hide_parameters=True,
                connect_args=connect_args,
            )
            self.session_factory = session_builder(
                self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
        except ImportError:
            if self.engine is not None:
                await self.close()
            raise StorageError("storage_dependency_missing") from None
        except Exception:  # noqa: BLE001 - 构造失败先清理再返回固定错误
            if self.engine is not None:
                await self.close()
            raise DatabaseConfigurationError() from None
        self.state = "initialized"

    def require_ready(self):
        if self.state == "disabled":
            raise StorageError("storage_disabled")
        if self.state in {"closing", "closed"}:
            raise StorageError("storage_closing")
        if self.state != "initialized":
            raise StorageError("storage_not_initialized")

    async def _clean_session(self, lease):
        failure = False
        try:
            await lease.session.rollback()
        except Exception:  # noqa: BLE001 - rollback失败仍必须close
            failure = True
        finally:
            try:
                await lease.session.close()
            except Exception:  # noqa: BLE001 - 清理异常不能携带驱动原文
                failure = True
        if failure:
            raise StorageError("storage_cleanup_failed") from None

    async def _release(self, lease):
        if lease.cleanup_task is None:
            lease.cleanup_task = asyncio.create_task(self._clean_session(lease))
        try:
            await _wait_cleanup(lease.cleanup_task)
        finally:
            if lease.cleanup_task.done():
                self._leases.discard(lease)

    @asynccontextmanager
    async def session(self):
        self.require_ready()
        lease = _Lease(self.session_factory())
        self._leases.add(lease)
        try:
            yield lease.session
        except BaseException as original:
            cleanup_succeeded = True
            try:
                await self._release(lease)
            except StorageError as exc:
                cleanup_succeeded = False
                LOGGER.warning("%s", exc.code)
            if isinstance(original, Exception):
                from sqlalchemy.exc import DBAPIError
                from sqlalchemy.exc import TimeoutError as PoolTimeout

                # asyncpg 的连接失败在某些路径上会以原始 OSError 冒泡（如连接被拒、
                # DNS 解析失败），不会包成 DBAPIError；同样属于「数据库不可用」。
                if isinstance(original, (DBAPIError, PoolTimeout, OSError)):
                    code = (
                        "storage_pool_timeout"
                        if isinstance(original, PoolTimeout)
                        else "storage_unavailable"
                    )
                    from app.persistence.failure_diagnostics import (
                        safe_database_metadata,
                    )

                    sqlstate = safe_database_metadata(original)
                    # 仅连接池等待失败或服务器明确回滚的事务允许完整重放。
                    # 连接故障可能发生在 COMMIT 后，不能推断事务已回滚。
                    replay_safe = cleanup_succeeded and (
                        isinstance(original, PoolTimeout)
                        or sqlstate in {"40001", "40P01"}
                    )
                    raise StorageError(
                        code,
                        retryable=replay_safe,
                        sqlstate=sqlstate,
                        replay_safe=replay_safe,
                    ) from None
            raise
        else:
            await self._release(lease)

    async def check_connection(self):
        self.require_ready()
        try:
            async with asyncio.timeout(self.settings.health_timeout):
                async with self.engine.connect() as connection:
                    result = await connection.exec_driver_sql("SELECT 1")
                    if result.scalar_one() != 1:
                        raise StorageError("storage_unavailable", retryable=True)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise StorageError("storage_health_timeout", retryable=True) from None
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - 健康检查只公开固定错误码
            # 只在已启用数据库的路径导入；不把底层异常内容放进日志或HTTP。
            from sqlalchemy.exc import TimeoutError as PoolTimeout

            code = (
                "storage_pool_timeout"
                if isinstance(exc, PoolTimeout)
                else "storage_unavailable"
            )
            raise StorageError(code, retryable=True) from None

    async def _dispose(self):
        self.state = "closing"
        failed = False
        try:
            for lease in tuple(self._leases):
                try:
                    await self._release(lease)
                except StorageError:
                    failed = True
            if self.engine is not None:
                try:
                    await self.engine.dispose()
                except Exception:  # noqa: BLE001 - dispose故障必须安全报告
                    failed = True
        finally:
            self.session_factory = None
            self.state = "closed"
        if failed:
            raise StorageError("storage_cleanup_failed") from None

    async def close(self):
        if self._close_task is None:
            # 在交出事件循环之前拒绝新Session，避免关闭任务尚未执行的空窗。
            self.state = "closing"
            self._close_task = asyncio.create_task(self._dispose())
        await _wait_cleanup(self._close_task)
