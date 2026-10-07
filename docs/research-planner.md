# Research Planner 与 EvidenceRequirement

本阶段只实现：问题 → 动态维度 → 确定性 Paper × Dimension 展开 → 按论文分组的 WorkerTask。没有 Worker 执行、Reviewer、Synthesis、异步编排或多 Agent Runtime；现有检索和 Agent API 均不修改。

## 使用接口

```python
from app.papers.catalog import load_paper_catalog
from app.research.planner import ResearchPlanner
from app.research.requirements import research_completion

# llm 是现有 LLMClient 实现。
planner = ResearchPlanner(llm, load_paper_catalog())
result = planner.prepare(question, candidate_paper_ids)
plan = result.plan
requirements = result.requirements
worker_tasks = result.worker_tasks
completion = research_completion(requirements)
```

plan() 只生成 ResearchPlan；prepare() 在一次规划调用后执行普通 Python 展开和分组。候选 ID 必须已经存在于 Paper Catalog，不把 BLIP-2 等别名自动改写为版本化 ID。可选 compact_paper_metadata 只接受候选的 paper_id/title，不读取全文、画像、Gold 或检索结果。

## 数据与职责

- ResearchDimension：dimension_id、name、description、research_question。ID 为唯一 snake_case；没有固定维度词表，默认最多 8 个，不要求填满。
- ResearchPlan：原问题、候选 paper_ids、维度。LLM 只提交 dimensions，问题和目标 ID 由程序绑定。
- EvidenceRequirement：requirement_id、paper_id、dimension_id、question、required、status、claim、evidence_ids。默认 required=True、pending、无 claim 和证据。
- WorkerTask：worker_task_id、paper_id、requirements。一篇论文一个任务，不能混入其他论文的需求，没有最终答案字段。
- PlannerTrace：开始/结束事实、目标数、维度 ID、需求/任务数量、模型调用、耗时、错误和已脱敏详情。

requirement_id 按 paper_id + dimension_id 的规范 JSON 生成 SHA-256 稳定 ID；Worker ID 按 paper_id 生成。相同计划重建时不会变化；这些 ID 以当前计划为作用域，不应据此跨不同研究问题复用已有 claim。

## Planner Prompt Contract

Your job is to identify what needs to be investigated, not to predict what the papers will say.

协议要求维度直接服务用户意图、彼此具有不同信息需求，研究问题以单篇论文为对象。名称、描述和问题都不得预测组件或写入未验证论文事实，也不应罗列用户没有提供的候选答案。窄问题不能为了补背景而增加其他结果或评估方向。跨论文比较不作为重复全部轴的额外维度。

模型通过唯一 submit_research_dimensions 动作输出严格 Schema。程序拒绝空/重复/超限维度、缺字段、额外 expected_answer 等字段及错误协议，不切片、不猜测修复。语义相关性和中立性仍是模型策略问题，不能由结构校验证明；本阶段通过人工检查 smoke 暴露边界，不增加 Reviewer。

## 需求状态和完成语义

| 状态 | claim | evidence_ids | Resolved |
| --- | --- | --- | --- |
| pending | None | [] | 否 |
| supported | 非空 | 非空且去重 | 是 |
| insufficient_evidence | None | [] | 是 |
| execution_failed / cancelled | None | [] | 否，本阶段仅预留 |

ResolutionRate=resolved/total，Coverage=supported/total。全部 resolved 才算 ResearchComplete，不要求全部 supported。8 supported + 2 insufficient → 100% resolution、80% coverage、complete=True；8 supported + 1 insufficient + 1 pending → 90% resolution、80% coverage、complete=False。空需求集合的两项比例为 0，complete=False，避免空计划被当作完成。

本阶段不校验 supported 的 Evidence ID 是否真实存在；这里只验证状态结构约束。未来执行层仍须接入已有 Evidence Resolver/Citation 验证，不能把结构合规当成证据真实性。

## 错误与 Trace

invalid_arguments、unknown_paper、invalid_plan、dependency_failure 进入明确异常路径。参数/未知 ID 错误发生在模型调用前；模型失败不自动重试。Schema 失败提供字段位置和类型，Provider 失败复用现有安全诊断，过滤 API key、路径、原始输入及异常堆栈。

Trace 的 requirement_count/worker_task_count 在 plan() 中是确定性计划数量；prepare() 会实际生成对象并核对数量。Planner latency 覆盖模型及计划校验，不包含 Worker 执行。模型正文与隐藏推理均不持久化，系统 Prompt 不进入运行结果。

## 真实 smoke 与限制

```bash
python -m scripts.smoke_research_planner --env-file .env \
  --output artifacts/research-planner-smoke.json
```

CLI 输出 question、paper_ids、维度及问题、完整需求、各 Worker 的 requirement IDs、调用数和耗时。每题只调用模型一次，生成任务后停止。semantic_review 默认待人工检查，不会自动声称语义通过。

最终三条运行分别为 5×3=15、5×3=15、4×2=8 条需求，分成 3/3/2 个 WorkerTask。实际问题均为单论文调查，不写入 Q-Former/Perceiver Resampler 等具体答案。窄训练成本问题只涉及参数数量、训练资源、成本相关设计和效率比较，没有单独性能/安全/幻觉维度。

两条广问题的 description 仍罗列接口组件或任务例子，尽管并未给每篇论文指认答案，仍可能形成引导，因此人工审核记录语义警告。WorkerTask 只获得 research_question 和维度 ID，不获得这些 description，但不能由此保证未来 Planner 一直中立。

开发共运行三轮、9 次真实模型请求，保留各轮原始结果：首轮出现一次依赖失败、描述答案提示及窄问题 Scope Drift；第二轮结构通过但有多论文问题被复制给单论文 Worker 的问题；第三轮通过通用策略约束使问题按论文调查。没有将错误结果删除，也没有实现自动重试或按题目硬编码维度。更具体的结果、耗时和人工语义检查位于本次生成的验收报告。

全量 372 项测试通过，原有 328 项保留，新增 44 项。Fake 测试验证结构、失败路径、稳定 ID、分组和完成统计；它不能证明真实模型语义可靠。
