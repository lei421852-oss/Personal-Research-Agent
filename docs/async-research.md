# 有界异步 Research Worker Pool

输入为已经生成的 WorkerTask[]，输出为 WorkerResult[]。一篇论文一个 Worker，多个需求共享同一份 paper-local 状态和证据。不生成跨论文结论，不执行 Reviewer/Synthesis，不修改已冻结 Planner、Retriever、Profile、Expansion、工具或 API/UI。

```mermaid
flowchart TB
    T[WorkerTasks] --> O[AsyncResearchOrchestrator]
    O --> W[Worker Semaphore]
    W --> P[PaperResearchWorker · paper-local state]
    P --> L[LLM resource gate]
    P --> V[VLM resource gate]
    P --> R[Retrieval resource gate]
    L --> A[Async I/O or centralized to_thread adapter]
    V --> A
    R --> A
    A --> E[Existing LLM / ToolExecutor / Evidence capabilities]
    E --> P
    P --> OUT[Typed WorkerResults · preserved partial progress]
```

## 模块边界

| 模块 | 职责 |
| --- | --- |
| models.py | 追加 WorkerStatus、WorkerError、WorkerTrace、WorkerResult、OrchestratorTrace，不改变原 Planner 模型 |
| worker.py | 单论文共享状态、工具循环、状态更新、scope/citation 校验和硬预算 |
| orchestrator.py | 有界并发、按输入顺序返回、故障隔离、取消快照和池统计 |
| runtime.py | Runtime Config 与独立 ResourceGate |
| retry.py | 明确失败分类、注入式退避及依赖计数 |
| async_tools.py | 原生异步直 await；集中 offload 同步 Provider/ToolExecutor |

Worker 可调用既有 search_text/find_figure/read_page/analyze_figure。内部 resolve_requirement/resolve_requirements 是需求更新动作，不是新的全局 Agent Tool，不执行检索。批量更新先完整校验再提交；同一证据可以支持多个需求。所有需求 resolved 后确定性结束，无需额外模型 finish。

## 状态与身份

supported 必须有 claim 和非空 evidence_ids；引用必须已经观察、在当前 Corpus 中存在且属于当前 Worker 的论文。成功 Tool 返回未知、越界或被改写证据属于执行故障，不能被转成业务空结果。此阶段验证身份与来源，不证明 claim 的语义支持充分性。

insufficient_evidence 是合法 resolved 结果，无 claim/证据。模型/工具依赖失败或预算耗尽则将剩余 pending 转为 execution_failed；取消转为 cancelled。已有 supported/insufficient 项保留，原任务的 paper_id、requirement ID、问题、维度和 required 不得改写。WorkerError 记录影响的 pending IDs 和公开原因。

| WorkerStatus | 含义 |
| --- | --- |
| completed | 全部需求 resolved，允许其中全部 insufficient |
| partial_success | 有 resolved 项，也有 execution_failed/cancelled 等未解决项 |
| failed | 没有有效 resolved 结果 |
| cancelled | 上层取消，至少有未完成项转为 cancelled |

## 预算与上下文

当前默认值集中在 ResearchRuntimeConfig：

| 配置 | 默认值 |
| --- | ---: |
| max_concurrent_workers | 3 |
| max_concurrent_llm | 2 |
| max_concurrent_vlm | 1 |
| max_concurrent_retrieval | 1 |
| max_tool_calls_per_worker | 8 |
| max_model_calls_per_worker | 20 |
| recent_observations | 4 |
| max_context_evidence | 20 |
| operation_timeout_seconds | 60 |
| max_retries | 2 |

共享 CPU Retriever 默认限流为 1，避免同一模型并行争抢。Worker Semaphore 限制执行槽，不等于线程数；资源槽也不等于 Worker 数。每次执行使用新的 Gate 才能得到本次资源 peak；复用 Gate 时其资源 peak 是 Gate 生命周期统计。

模型只接收未解决问题、已解决摘要、去重证据引用和最近 Observation，不接收无限增长的完整对话。上下文字符串副本有长度限制，原证据和最终 claim 不被截断。相同成功/空 lookup 不再次执行，通过 redundant_action 反馈让模型改动或复用。

tool_calls 统计进入 ToolExecutor 的物理执行尝试，重试也消耗工具预算；Controller 拒绝的参数和需求更新不计工具调用。model_calls 是保守的模型预算计数：LLM 适配调用 + analyze_figure 执行尝试，分别提供 llm_calls、vlm_tool_attempts。VLM 预处理可能在实际 client 调用前失败，因此这些数不是 Provider 计费次数；取消结果也是当时快照。VLM 重试同时受剩余模型/工具预算限制。

## 重试和取消

RetryPolicy 只对 Timeout/Connection、HTTP 429/502/503 等明确瞬态失败重试。HTTP 400/401、未知输入、非法参数不做 Provider Retry；empty/insufficient 是业务结果。不能因为冻结 Observation.issue.retryable=True 就无条件重试。采用 d0×2^n+jitter，sleeper/clock/jitter 可注入；Provider 自带 retry 保持 0。

模型重试位于 LLM adapter，工具重试位于 sync ToolExecutor 的适配边界。只重试资源调用，不重跑整个 Worker。硬预算包含物理重试；预算提前限制重试次数时保留 budget_exhausted 原因。

取消 Orchestrator asyncio.Task 会取消子 Worker；Worker 先保存 last_result，池保存 last_results/trace，再重新抛出 CancelledError。普通 Exception handler 不吞掉取消，也不会因为一个普通 Worker 故障丢失其他结果。

**取消等待 asyncio.to_thread 的协程，不保证底层线程终止。** 已启动线程继续持有资源槽，实际返回后才释放；外层等待超时不会重叠启动另一次线程调用。真实 Provider 配置自己的 bounded timeout；冻结 CPU Retrieval 没有强制中断能力。关闭客户端前可 await worker.drain()。CLI 会记录取消结果后等待残留阻塞操作清理，因此退出可能仍等待已有请求到达超时；cleanup_latency_ms 单独记录。

## 使用与真实 Smoke

```python
config = ResearchRuntimeConfig()
gate = ResourceGate(config)
# factory(task) 返回私有 PaperResearchWorker，内部两个适配器共享这个 gate。
orchestrator = AsyncResearchOrchestrator(factory, config, gate)
results = await orchestrator.run(worker_tasks)
```

Runner 接受 ResearchPlan、ResearchPreparation 或既有 Planner smoke JSON 中的一个 case。不会重新生成维度，不调用 Planner。每个 Worker 私有 Provider、ToolRegistry、ToolExecutor 和状态，只共享只读语料、Resolver、冻结 TextRetriever 与 Gate。

```bash
# 仅检查计划：不读凭据、不初始化模型、不执行 Worker。
python -m scripts.smoke_async_research --plan artifacts/research-planner-smoke.json \
  --case-id planner-smoke-01 --validate-only --output artifacts/worker-plan-validation.json

# 使用者显式执行真实调用。
python -m scripts.smoke_async_research --plan artifacts/research-planner-smoke.json \
  --case-id planner-smoke-01 --env-file .env --max-workers 3 \
  --max-llm 2 --max-vlm 1 --max-retrieval 1 \
  --output artifacts/async-research-smoke.json
```

其他可调 CLI 参数包括 --max-tool-calls、--max-model-calls、--max-retries、--operation-timeout。输出 Worker/requirement 状态、证据 IDs、预算/重试/错误、Worker 和资源 peaks 及延迟。

serial_estimated_latency_ms 是并行运行中各 Worker latency 的和，包含资源队列等待；actual_parallel_latency_ms 是池执行耗时。前者是代理估计，不是另跑一次串行实验，不可据此声称正式性能提升。默认 BLIP-2/LLaVA 只有发现元数据，没有 prepared 正文/图像；它们可以完成为 insufficient，不能产生伪引用。

## 指标与验证

ResolutionRate=resolved requirements/total；Coverage=supported/total；WorkerSuccessRate=completed workers/total；PartialFailureRate=(partial_success+failed)/total。取消 Worker 不归入 partial failure 分子，但仍在总 Worker 分母。RetryRate=发生重试的逻辑依赖调用/全部逻辑依赖调用，物理 retry_count 单独记录。

新增 101 项 Fake/确定性测试，原有 372 项保留，全量 473 passed。事件屏障验证 10 tasks / cap=3 真重叠及独立 Worker/resource caps，覆盖 shared evidence、scope/citation、原子 batch、partial failure、瞬态/永久重试、注入式退避、物理预算、取消和线程资源槽保留。不依赖真实模型或实际退避 sleep。Ruff、formatter 和冻结文件校验通过。

本次仅验证真实 Runner 的计划模式，没有执行真实 Worker smoke、正式研究实验或 Benchmark。没有 Reviewer、Synthesis、跨论文答案、Checkpoint、消息队列、ProcessPool 或新检索算法。
