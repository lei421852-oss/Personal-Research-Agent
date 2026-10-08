# Stage G1 工程现状清单

核验时间：2026-10-09。审计起点：`3be3a68a87c72a22456908c9b655afcfc8c422e5`。本清单来自源码、配置与只读语料加载；不根据历史计划推断功能完成情况。

## 实际任务与入口

| 任务 | 真实入口 | 默认行为与限制 |
| --- | --- | --- |
| 单问题论文 QA / Figure QA | [app/main.py](../../app/main.py)、[API runtime](../../app/api/runtime.py)、[路由](../../app/api/routes/agent.py) | `POST /api/agent/run` → `BoundedToolAgent`；UI 不运行多论文 Worker Pool。默认 `day5-controlled` 检索；真实排序须显式选择 `real`。 |
| 论文元数据发现 | [PaperRetriever](../../app/papers/retriever.py)、[发现工具](../../app/tools/discovery.py) | 独立 Paper Index，返回候选论文，不能生成正文引用。API 需显式开启 `AGENT_PAPER_DISCOVERY=1`。 |
| Agent 控制查询扩展 | [QueryExpander](../../app/papers/expansion.py)、[查询约束](../../app/agent/query_guard.py) | 一次研究请求最多一次扩展、最多两条新 Query，保留原 Query；工具不自行检索。 |
| 论文画像准备 | [profiles.py](../../app/papers/profiles.py)、[ingest_papers.py](../../scripts/ingest_papers.py) | Title + Abstract 离线抽取结构化画像；缓存复用；不在检索时调用 LLM。 |
| 动态研究需求生成 | [planner.py](../../app/research/planner.py)、[requirements.py](../../app/research/requirements.py) | 接受已选论文 ID；模型提出中性维度，程序展开 Paper × Dimension。Planner 不负责自动下载或检索论文。 |
| 单论文有界研究与多 Worker 并发 | [worker.py](../../app/research/worker.py)、[orchestrator.py](../../app/research/orchestrator.py) | 每篇论文一个 WorkerTask；资源槽和硬预算由程序控制。 |
| 审核、一次定向恢复、综合报告 | [evaluate_cross_paper.py](../../scripts/evaluate_cross_paper.py) 的 `run_case` | 使用保存的计划，依次组合 Worker → Reviewer → Recovery → Synthesis；CLI 默认仅预检。真实执行会打开/补建配置索引，G1 不运行该模式。 |
| 质量基线执行与评分 | [run_quality_benchmark.py](../../scripts/run_quality_benchmark.py)、[score_quality_benchmark.py](../../scripts/score_quality_benchmark.py) | 真实 Provider 执行与离线评分分开；候选、AI 参考、人工 Gold 分开。 |
| 覆盖增强实验 | [coverage_execution.py](../../eval/research_quality/coverage_execution.py)、[run_worker_coverage_comparison.py](../../scripts/run_worker_coverage_comparison.py) | 显式启用 `CoverageAwarePaperWorker`，初始和 Recovery Worker 均使用它；不替换上述默认入口。运行脚本会真实调用 Provider。 |

## 模块与契约

| 模块 | 核心源文件 | 输入 → 输出 / 职责 |
| --- | --- | --- |
| 单 Agent | [loop.py](../../app/agent/loop.py)、[context.py](../../app/agent/context.py)、[finish.py](../../app/agent/finish.py) | Question → AgentState / Decision Context → ToolCall / finish → AgentResult；模型判断语义充分性。 |
| Tool 层 | [registry.py](../../app/tools/registry.py)、[executor.py](../../app/tools/executor.py)、[observations.py](../../app/tools/observations.py) | 参数校验、执行、公共 Observation；success / empty / error 与 issue 分开。 |
| 证据领域 | [models.py](../../app/evidence/models.py)、[corpus.py](../../app/evidence/corpus.py)、[resolver.py](../../app/evidence/resolver.py) | Prepared Corpus → TextEvidence / FigureEvidence；Evidence ID → 原记录和来源。 |
| 文本检索 | [retriever.py](../../app/retrieval/retriever.py)、[vector_store.py](../../app/retrieval/vector_store.py)、[ingestion.py](../../app/retrieval/ingestion.py) | query + paper_ids + top_k → RetrievedEvidence；范围过滤在 Top-K 前生效。 |
| 论文发现 | [models.py](../../app/papers/models.py)、[store.py](../../app/papers/store.py)、[catalog.py](../../app/papers/catalog.py) | PaperDocument / RetrievedPaper，独立 Chroma collection；模型与 fixture 来源绑定。 |
| 图像分析可选路径 | [analysis.py](../../app/vision/analysis.py)、[provider.py](../../app/vision/provider.py)、[figure.py](../../app/tools/figure.py) | 受控 Figure ID + Caption + Text Context → 图片请求 → 结构化分析；不是任意路径或通用 PDF 读取。 |
| 引用身份校验 | [citations.py](../../app/evidence/citations.py) | 已提供 Evidence 与模型引用集合的子集关系；不证明自然语言语义蕴含。 |
| 研究计划 / 需求 / 结果 | [research/models.py](../../app/research/models.py) | ResearchPlan、ResearchDimension、EvidenceRequirement、WorkerTask、WorkerResult、WorkerTrace。 |
| 并发和基础设施 | [runtime.py](../../app/research/runtime.py)、[async_tools.py](../../app/research/async_tools.py)、[retry.py](../../app/research/retry.py) | ResourceGate + async adapters；同步依赖通过 `asyncio.to_thread` offload；有界 transient retry、取消和 drain。 |
| Reviewer | [review_validation.py](../../app/research/review_validation.py)、[reviewer.py](../../app/research/reviewer.py)、[review_runtime.py](../../app/research/review_runtime.py) | WorkerResult / Requirement → ReviewTask → ReviewResult；结构检查先于模型语义审核。 |
| Recovery | [recovery_eligibility.py](../../app/research/recovery_eligibility.py)、[recovery_orchestrator.py](../../app/research/recovery_orchestrator.py)、[recovery_merge.py](../../app/research/recovery_merge.py) | 精确绑定初始拒绝、反馈与预算 → RecoveryTask → 重新审核 → 合并；历史不覆盖，最多一轮。 |
| Synthesis | [synthesis_input.py](../../app/research/synthesis_input.py)、[synthesis_agent.py](../../app/research/synthesis_agent.py)、[synthesis_validator.py](../../app/research/synthesis_validator.py)、[synthesis_runtime.py](../../app/research/synthesis_runtime.py) | 已审核记录 → VerifiedClaim / MissingEvidenceStatus → 结构化 Proposal → 程序组装 ResearchReport。 |
| Coverage 实验 | [coverage_models.py](../../app/research/coverage_models.py)、[coverage_worker.py](../../app/research/coverage_worker.py) | covered / missing / not_applicable、阶段覆盖和来源摘录；保持原 WorkerResult 状态兼容，另存诊断。 |

## 数据、配置与依赖

只读加载确认：

| 数据集合 | 路径 | 规模与用途 |
| --- | --- | --- |
| V0.1 Prepared Corpus | [data/papers](../../data/papers) | CLIP、BLIP、Flamingo：3 篇，25 条文本、3 条图像证据。 |
| 研究追加语料 | [data/research_papers](../../data/research_papers) | BLIP-2、LLaVA；与前者合计 5 篇、46 条文本、5 条图像证据。 |
| Paper Catalog | [papers.json](../../data/paper_discovery/papers.json) | 25 篇元数据；不代表 25 篇均有 Prepared PDF Evidence。 |
| 人工准备配置 | [fixture_configs](../../data/fixture_configs) | 固定 PDF 版本、人工文本区域与 Figure crop。不是自动 PDF Parser。 |
| Stage E 研究配置 | [cross_paper_eval.json](../../configs/cross_paper_eval.json) | Qwen/Qwen3-Embedding-0.6B、CPU、cosine、Top-K=10、research-stage-e-text collection。 |
| 质量评测 | [eval/research_quality](../../eval/research_quality)、[data/benchmark](../../data/benchmark) | Schema、校验、执行、评分、AI 辅助评分、分组隔离和来源验证。 |

研究语料 snapshot 为 `18fce87eb4050186aefc545a97b55d8b397108953647c5985c685102b2ea2390`。正文索引与论文元数据索引独立；本轮未打开 Chroma 写接口，未验证新的检索排名。

依赖以 [pyproject.toml](../../pyproject.toml) 为准：Python ≥3.11，基础 Pydantic；`test`、`retrieval`、`llm`、`vision`、`data`、`api`、`ui-test` 为可选组。当前包版本元数据仍为 `0.1.0`，不能据研究阶段名称声称已发布 V0.2 包。Setuptools 只打包 `app*`，`scripts` / `eval` 的运行须基于源码仓库。

外部依赖：Tool Calling LLM、支持图片输入及 JSON 的 VLM（使用该工具时）、Embedding 模型本地缓存或首次下载、磁盘上的 Chroma。没有 Redis、Celery、LangGraph 或远程任务服务依赖。实际环境版本记录于 [baseline.json](../acceptance/stage_g1/baseline.json)。

## 测试与历史结果

- `python -m pytest -q`：既有全量测试，覆盖 API、工具、并发、审核、恢复、综合、质量评分和 Coverage 实验。
- `ruff check app tests scripts eval` / `ruff format --check app tests scripts eval`：沿用当前工程范围；项目没有配置独立 type-checker。
- [Stage E 冻结证据](../acceptance/stage_e_freeze)、[F2 原始执行与评分](../acceptance/quality_end_to_end/results)、[F3.1-E 七题对照](../acceptance/stage_f3_1_e)：历史产物，只读保留，不冒充本轮新实验。
- [G1 本轮验收](../acceptance/stage_g1/acceptance_report.md) 独立记录新执行的命令、退出码与日志。

已知边界：当前研究主要使用预处理文本与图注；Reviewer 不读取像素。元数据发现不保证正文可用。模型审核不等于独立人工事实判定；Coverage 的结构化完成不等于语义完整。API/UI 不提供多论文研究报告入口。详见 [系统总览](system-overview.md)。
