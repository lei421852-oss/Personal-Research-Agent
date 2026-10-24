"""每个 run 一个有界串行写入队列：事件、报告与屏障。

- 数据命令按数量限界（默认 256），满了立即触发故障 latch，不阻塞调用方。
- 屏障与数据同队列，保证「等待调用前已接受的全部命令」这一 FIFO 语义。
- 关闭走独立控制队列，可在数据洪峰下插队，不会被普通写入永久阻塞。
- 同一 run 严格串行；不同 run 各自持有 Writer，互不共享 AsyncSession。
- 只对可确认安全的瞬时存储故障重试；冲突类错误绝不重试。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.persistence.errors import StorageError
from app.persistence.repository import (
    AppendResult,
    EventCommand,
    RepositoryError,
)
from app.persistence.serialization import ReportCommand

LOGGER = logging.getLogger(__name__)

DEFAULT_CAPACITY = 256
DEFAULT_WRITE_TIMEOUT = 5.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_FLUSH_TIMEOUT = 10.0


class WriterError(RepositoryError):
    pass


class WriterFull(WriterError):
    def __init__(self) -> None:
        super().__init__("writer_queue_full")


class WriterClosed(WriterError):
    def __init__(self) -> None:
        super().__init__("writer_closed")


class WriterFailed(WriterError):
    def __init__(self, reason: str):
        super().__init__("writer_failed", detail=reason)


@dataclass(frozen=True)
class AppendEvents:
    run_id: str
    events: tuple[EventCommand, ...]
    expected_last_seq: int


@dataclass(frozen=True)
class SaveReport:
    run_id: str
    command: ReportCommand


@dataclass(frozen=True)
class BarrierResult:
    completed: int
    last_event_seq: int


@dataclass
class WriterStats:
    accepted: int = 0
    completed: int = 0
    failed: int = 0
    dropped: int = 0
    retried: int = 0


@dataclass(frozen=True)
class _Barrier:
    future: asyncio.Future


@dataclass(frozen=True)
class _Close:
    future: asyncio.Future


class RunWriter:
    """单 run 串行写入器；调用方在 lifespan 内 start / close。"""

    def __init__(
        self,
        repository,
        *,
        latch=None,
        capacity: int = DEFAULT_CAPACITY,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        flush_timeout: float = DEFAULT_FLUSH_TIMEOUT,
    ) -> None:
        if capacity < 1 or max_attempts < 1:
            raise ValueError("invalid writer limits")
        self.repository = repository
        self.latch = latch
        self.capacity = capacity
        self.write_timeout = write_timeout
        self.max_attempts = max_attempts
        self.flush_timeout = flush_timeout
        self.stats = WriterStats()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._control: asyncio.Queue = asyncio.Queue()
        self._pending = 0
        self._failure: BaseException | None = None
        self._closing = False
        self._task: asyncio.Task | None = None
        self._getter: asyncio.Task | None = None
        self._last_event_seq = 0

    # ---- 生命周期 -------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._consume(), name="persistence-writer")

    @property
    def last_event_seq(self) -> int:
        return self._last_event_seq

    @property
    def pending(self) -> int:
        return self._pending

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    @property
    def closed(self) -> bool:
        return self._closing

    # ---- 提交 -----------------------------------------------------------

    def submit(self, command) -> None:
        if self._closing:
            raise WriterClosed()
        self._require_writable()
        if self._pending >= self.capacity:
            self._trip("writer_queue_full")
            raise WriterFull()
        self._queue.put_nowait(command)
        self._pending += 1
        self.stats.accepted += 1

    async def barrier(self, timeout: float | None = None) -> BarrierResult:
        """等待调用前已接受的全部数据命令完成；失败则显式抛出。"""

        self._require_writable()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._queue.put_nowait(_Barrier(future))
        try:
            return await asyncio.wait_for(
                future, self.flush_timeout if timeout is None else timeout
            )
        except TimeoutError:
            self._trip("writer_flush_timeout")
            raise WriterFailed("flush_timeout") from None

    async def flush(self, timeout: float | None = None) -> BarrierResult:
        return await self.barrier(timeout)

    async def close(self, timeout: float | None = None) -> None:
        """停止接收并等待已入队命令处理完；控制队列可插队。"""

        if self._closing:
            return
        self._closing = True
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._control.put_nowait(_Close(future))
        limit = self.flush_timeout if timeout is None else timeout
        try:
            await asyncio.wait_for(future, limit)
        except TimeoutError:
            self._trip("writer_close_timeout")
            raise WriterFailed("close_timeout") from None
        finally:
            if self._getter is not None and not self._getter.done():
                self._getter.cancel()
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOGGER.debug("writer shutdown swallowed error", exc_info=True)
                self._task = None

    # ---- 内部 -----------------------------------------------------------

    def _trip(self, reason: str) -> None:
        if self.latch is not None:
            self.latch.trip(reason)

    def _require_writable(self) -> None:
        if self.latch is not None and self.latch.tripped:
            from app.persistence.coordinator import PersistenceLatched

            raise PersistenceLatched(self.latch.reason or "persistence_latched")

    async def _consume(self) -> None:
        while True:
            control = None
            if not self._control.empty():
                control = self._control.get_nowait()
            if control is not None:
                await self._handle_control(control)
                return

            if self._getter is None or self._getter.done():
                self._getter = asyncio.create_task(self._queue.get())
            control_waiter = asyncio.create_task(self._control.get())
            done, pending = await asyncio.wait(
                {self._getter, control_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                # 只取消控制等待者；数据 getter 保留到下一轮，避免丢弃已取到的命令。
                task.cancel()
            if control_waiter in done:
                item = control_waiter.result()
                await self._handle_control(item)
                return
            item = self._getter.result()
            self._getter = None
            await self._handle_data(item)

    async def _handle_control(self, item) -> None:
        if isinstance(item, _Close):
            # 关闭时仍未处理的命令计入 dropped，不能假装它们已持久化。
            self.stats.dropped += max(0, self._pending)
            self._pending = 0
            self._resolve(
                item.future, BarrierResult(self.stats.completed, self._last_event_seq)
            )

    async def _handle_data(self, item) -> None:
        if isinstance(item, _Barrier):
            if self._failure is not None:
                self._reject(item.future, WriterFailed("writer_failed"))
            else:
                self._resolve(
                    item.future,
                    BarrierResult(self.stats.completed, self._last_event_seq),
                )
            return

        self._pending -= 1
        if self._failure is not None:
            self.stats.dropped += 1
            return
        try:
            await self._execute(item)
            self.stats.completed += 1
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - 失败必须显式记录
            self._failure = exc
            self.stats.failed += 1
            self._trip(
                f"writer_write_failed:{getattr(exc, 'code', type(exc).__name__)}"
            )

    @staticmethod
    def _resolve(future: asyncio.Future, value) -> None:
        if not future.done():
            future.set_result(value)

    @staticmethod
    def _reject(future: asyncio.Future, error: BaseException) -> None:
        if not future.done():
            future.set_exception(error)

    async def _execute(self, command) -> None:
        attempts = 0
        while True:
            attempts += 1
            try:
                await asyncio.wait_for(self._apply(command), self.write_timeout)
                return
            except TimeoutError:
                error: BaseException = WriterFailed("write_timeout")
            except RepositoryError:
                # 冲突类错误（幂等键、版本、序号、指针）不重试。
                raise
            except StorageError as exc:
                if not exc.retryable:
                    raise
                error = exc
            if attempts >= self.max_attempts:
                raise error
            self.stats.retried += 1

    async def _apply(self, command) -> None:
        if isinstance(command, AppendEvents):
            result: AppendResult = await self.repository.append_events(
                command.run_id,
                list(command.events),
                expected_last_seq=command.expected_last_seq,
            )
            self._last_event_seq = max(self._last_event_seq, result.last_event_seq)
            return
        if isinstance(command, SaveReport):
            await self.repository.save_report_revision(command.run_id, command.command)
            return
        raise WriterFailed("unknown_command")
