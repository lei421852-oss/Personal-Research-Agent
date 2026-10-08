"""固定数量异步消费者执行证据审核；不触发 Worker 恢复或跨论文生成。"""

import asyncio
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from app.research.review_models import (
    ReviewIssue,
    ReviewPoolTrace,
    ReviewResult,
    ReviewTask,
    ReviewTrace,
)
from app.research.runtime import PositiveInt, ResourceGate


class ReviewRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_concurrent_reviewers: PositiveInt = 2
    max_model_calls_per_review: PositiveInt = 3


class EvidenceReviewRuntime:
    def __init__(
        self,
        reviewer_factory,
        validator,
        config: ReviewRuntimeConfig | None = None,
        resource_gate: ResourceGate | None = None,
        *,
        clock=perf_counter,
    ):
        self.factory, self.validator = reviewer_factory, validator
        self.config = config or ReviewRuntimeConfig()
        self.resource_gate = resource_gate or ResourceGate()
        self.clock = clock
        self.last_results: list[ReviewResult] = []
        self.trace: ReviewPoolTrace | None = None
        self.reviewers = []
        self._running = False

    @staticmethod
    def _failed(task, code="internal_failure", trace=None):
        """执行故障不能冒充证据不支持；不公开异常正文或隐藏推理。"""
        return ReviewResult(
            worker_task_id=task.worker_task_id,
            requirement_id=task.requirement.requirement_id,
            paper_id=task.requirement.paper_id,
            status="not_reviewable",
            reason_code=None,
            claim=task.requirement.claim,
            evidence_ids=task.requirement.evidence_ids,
            validation_status="passed",
            issue=ReviewIssue(
                code=code,
                message="Review execution was cancelled."
                if code == "cancelled"
                else "Reviewer execution failed; no semantic verdict was produced.",
            ),
            trace=trace or ReviewTrace(),
        )

    def _execution_trace(self, reviewer, before, started):
        """异常兜底仍保留已发生的物理调用；不重试或读取异常正文。"""
        adapter = getattr(reviewer, "llm", None)
        after = (
            adapter.snapshot() if adapter is not None and before is not None else None
        )
        return ReviewTrace(
            model_calls=max(0, after["total_attempts"] - before["total_attempts"])
            if after is not None
            else 0,
            retry_count=max(0, after["retries"] - before["retries"])
            if after is not None
            else 0,
            latency_ms=max(0, (self.clock() - started) * 1000),
        )

    def _snapshot(self, results, reviewable, bypassed, peak, started):
        self.last_results = list(results)
        passed = sum(r.status == "passed" for r in results)
        rejected = sum(r.status == "rejected" for r in results)
        self.trace = ReviewPoolTrace(
            total_requirement_count=len(results),
            reviewable_requirement_count=reviewable,
            reviewed_count=passed + rejected,
            passed_count=passed,
            rejected_count=rejected,
            not_reviewable_count=sum(r.status == "not_reviewable" for r in results),
            bypassed_count=bypassed,
            reviewer_model_calls=sum(r.trace.model_calls for r in results),
            retry_count=sum(r.trace.retry_count for r in results),
            peak_reviewer_concurrency=peak,
            # ResourceGate 的峰值属于 Gate 生命周期；真实 Smoke 为审核阶段新建 Gate。
            peak_llm_concurrency=self.resource_gate.peaks["llm"],
            total_latency_ms=max(0, (self.clock() - started) * 1000),
        )

    async def run(self, worker_results):
        if self._running:
            raise RuntimeError(
                "An EvidenceReviewRuntime cannot run two batches concurrently"
            )
        self._running = True
        self.last_results, self.trace, self.reviewers = [], None, []
        started = self.clock()
        results, jobs = [], []
        active, peak = 0, 0
        consumers = []
        try:
            # 先完整执行程序校验。无需语义审核的结果不创建 Reviewer 或模型客户端。
            for worker in worker_results:
                for requirement in worker.requirements:
                    outcome = self.validator.validate(worker, requirement)
                    index = len(results)
                    if isinstance(outcome, ReviewTask):
                        jobs.append((index, outcome))
                        results.append(None)
                    else:
                        results.append(outcome)
            pending = iter(jobs)
            semaphore = asyncio.Semaphore(self.config.max_concurrent_reviewers)

            async def consume():
                nonlocal active, peak
                while True:
                    # 获取任务到进入槽位之间没有 await，单事件循环不会重复分配索引。
                    job = next(pending, None)
                    if job is None:
                        return
                    index, task = job
                    async with semaphore:
                        active += 1
                        peak = max(peak, active)
                        reviewer = None
                        before = None
                        review_started = self.clock()
                        try:
                            reviewer = self.factory(task)
                            if reviewer.resource_gate is not self.resource_gate:
                                raise ValueError(
                                    "Reviewers must share the review runtime ResourceGate"
                                )
                            if (
                                reviewer.max_model_calls
                                != self.config.max_model_calls_per_review
                            ):
                                raise ValueError(
                                    "Reviewer model budget differs from runtime configuration"
                                )
                            self.reviewers.append(reviewer)
                            adapter = getattr(reviewer, "llm", None)
                            before = adapter.snapshot() if adapter is not None else None
                            result = await reviewer.run(task)
                            if not isinstance(result, ReviewResult):
                                raise TypeError(
                                    "Reviewer returned an invalid result type"
                                )
                            result = ReviewResult.model_validate(
                                result.model_dump(mode="python", warnings=False)
                            )
                            # 身份和 Claim 不由 Reviewer 改写；只接收当前任务的语义裁决。
                            if (
                                result.worker_task_id != task.worker_task_id
                                or result.requirement_id
                                != task.requirement.requirement_id
                                or result.paper_id != task.requirement.paper_id
                                or result.claim != task.requirement.claim
                                or result.evidence_ids != task.requirement.evidence_ids
                            ):
                                raise ValueError(
                                    "Review result does not match its validated task"
                                )
                            results[index] = result
                        except asyncio.CancelledError:
                            results[index] = getattr(
                                reviewer, "last_result", None
                            ) or self._failed(
                                task,
                                "cancelled",
                                self._execution_trace(reviewer, before, review_started),
                            )
                            raise
                        except Exception:  # noqa: BLE001 - 隔离单个 Reviewer，保留同批其他结果
                            results[index] = self._failed(
                                task,
                                trace=self._execution_trace(
                                    reviewer, before, review_started
                                ),
                            )
                        finally:
                            active -= 1

            count = min(len(jobs), self.config.max_concurrent_reviewers)
            # gather 的 Task 数由固定配置限定，不随需求总数无限增长。
            consumers = [asyncio.create_task(consume()) for _ in range(count)]
            if consumers:
                await asyncio.gather(*consumers)
            self._snapshot(results, len(jobs), len(results) - len(jobs), peak, started)
            return self.last_results
        except asyncio.CancelledError:
            for consumer in consumers:
                consumer.cancel()
            if consumers:
                await asyncio.gather(*consumers, return_exceptions=True)
            for index, task in jobs:
                if results[index] is None:
                    results[index] = self._failed(task, "cancelled")
            self._snapshot(results, len(jobs), len(results) - len(jobs), peak, started)
            raise
        finally:
            self._running = False

    async def drain(self):
        """关闭客户端前等待已启动阻塞操作返回；仍由现有适配边界管理线程。"""
        for reviewer in self.reviewers:
            await reviewer.drain()
