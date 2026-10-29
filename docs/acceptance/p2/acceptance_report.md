# P2 Agent 持久化记忆模块验收报告

日期：2026-10-29。结论：**PASS（本地工程范围）**。

已实现可选的项目级持久化记忆，完成真实隔离 PostgreSQL 验证与完整离线回归。本结论不代表真实模型已通过自主记忆质量评测，不代表已迁移用户开发库或部署生产。

## 1. 基线与授权

- 原分支：`feature/production-tool-integration`。
- 原 HEAD：`32465578d27df95b896817240309f782ab2b9312`。
- 本阶段分支：`feature/agent-memory`。开始时 tracked/staged 干净，保留原 13 个未跟踪文件。
- 用户确认完成后 push 阶段分支，由用户合并，覆盖附件中的旧“不 push”条款。只推送 `research-agent` 对应 Personal-Research-Agent，不推 origin、不合并。
- 用户授权临时 PostgreSQL 16：127.0.0.1:55437、personal_agent_memory_test、p2_test、随机密码、SCRAM、tmpfs，无旧数据挂载，仅本库迁移/记忆事务测试，结束销毁。

## 2. 实现与接口

审计及决策见 [audit-and-plan.md](audit-and-plan.md)，详细契约见 [agent-memory.md](../../architecture/agent-memory.md)。

1. `app/memory`：严格参数模型、来源与敏感内容策略、项目范围 Service、有限关键词/标签检索、四个内部工具，以及可选 finish/展示适配。
2. `app/persistence/memory_models.py` / `memory_repository.py`：复用现有数据库资源和短 Session，单表版本链、唯一约束、事务锁、冲突/过期/软删除及固定错误语义。
3. 新迁移 `p2_0001`，下接 `h2_0001`。独立 MemoryBase 与原 Base 组合用于迁移比较，原三表模型与原迁移保持不变。
4. 既有文件只改 `app/workbench.py`、`app/research/react_runtime.py`、`migrations/env.py`。没有改核心循环、主策略、Retriever、原 Registry/Executor 或多 Agent 升级条件。
5. 默认关闭；启用需显式项目 ID、数据库及已迁移 schema。四工具进入原预算/Observation/Trace，不建立 memory-first 固定流程。
6. Memory ID 不进入论文引用白名单；可选 memory-only finish 从已观察的权威记录组装答案，独立标记历史项目上下文。
7. 版本冲突、提交未知与 unavailable 不伪装成 empty。未知提交锁止本次同键后续写入，仅允许读核对；未解决写入失败不能交付为成功。

## 3. 实际测试

以下均为本轮实际执行，不引用历史通过数代替。53 项新增离线测试 + 19 项真实 PostgreSQL 测试 = **72 项新增测试**。

| 测试组 | passed | skipped | failure/error | 退出码 | 用时 |
|---|---:|---:|---:|---:|---:|
| targeted | 53 | 0 | 0 | 0 | 3.38s |
| related | 427 | 0 | 0 | 0 | 12.30s |
| postgresql-final | 19 | 0 | 0 | 0 | 3.07s |
| full | 1754 | 75 | 0 | 0 | 71.34s |

`targeted` 包含 53 项；`related` 427 项是既有相关回归；两者均包含于 full，不相加充当独立样本。full 的 75 项跳过是显式外部/数据库授权门控关闭；新增真库 19 项在独立授权进程执行并通过。跳过项目不算真库通过。

完整回归出现 1 条既有 Starlette/httpx TestClient 弃用警告，未为本阶段修改依赖。

### 实际执行入口

```bash
.venv/bin/python -B artifacts/p2-agent-memory-2026-10-29/run_offline_checks.py targeted
.venv/bin/python -B artifacts/p2-agent-memory-2026-10-29/run_offline_checks.py related
.venv/bin/python -B artifacts/p2-agent-memory-2026-10-29/run_offline_checks.py full
.venv/bin/python -B artifacts/p2-agent-memory-2026-10-29/run_postgresql_checks.py prepare
.venv/bin/python -B artifacts/p2-agent-memory-2026-10-29/run_postgresql_checks.py test --label postgresql-final
.venv/bin/python -B artifacts/p2-agent-memory-2026-10-29/run_postgresql_checks.py verify
.venv/bin/ruff check .
.venv/bin/ruff format --check app tests migrations
git diff --check
```

验收运行器位于忽略的 artifacts 目录，不是新增产品 CLI。离线运行器清除真实调用门控、禁用模型下载，并拦截非回环连接及数据库端口；full/targeted/related 被拦截外连次数均为 0。真库运行器只向固定授权目标传入私密 URL，日志脱敏。

静态检查：Ruff exit 0；格式检查 exit 0（287 文件格式符合）；diff check exit 0。文档完成后再次核对链接与差异。

### 关键覆盖

- 写入后由新 Repository/数据库资源读取；8 路同键同内容并发只有一个有效版本。
- 不同内容冲突、并发更新只有一方成功、完整不可变正文版本链。
- 软删除、TTL、已替代版本过滤；旧创建请求不复活已删除记录、不重置过期 TTL。
- 项目隔离、中文关键词/标签排序、Top-K、字面转义和非法参数。
- 数据库中途写入失败回滚；提交已发生但确认丢失时保持 unknown；锁等待有界；损坏记录不当成空。
- 取消等待写入后事务与资源释放。
- Scripted ReAct 使用真实 Repository；普通问题不强制记忆，多 Agent 入口不改变。
- Memory ID 伪装论文 Evidence 被拒，写失败不冒充成功，敏感记忆参数不进入 ToolTrace/执行事件明文。
- 启用缺少配置、启动失败清理、默认禁用不加载 SQLAlchemy/asyncpg。

这是确定性工程测试，不是真实 LLM 质量测量。没有付费模型、真实 arXiv、新研究任务、浏览器或已有数据库操作。

### 全量跳过分布

- `tests.integration.test_admission_postgresql`：3
- `tests.integration.test_arxiv_b2_live`：2
- `tests.integration.test_arxiv_live`：2
- `tests.integration.test_database_infrastructure_postgresql`：3
- `tests.integration.test_end_to_end_postgresql`：9
- `tests.integration.test_manager_persistence_postgresql`：2
- `tests.integration.test_memory_postgresql`：19
- `tests.integration.test_persistence_write_regressions_postgresql`：9
- `tests.integration.test_persistent_reads_postgresql`：6
- `tests.integration.test_repository_postgresql`：15
- `tests.integration.test_writer_postgresql`：5

### 开发中发现与修正

初轮自建测试中，桥接 fixture 曾传入 coroutine 而非 operation factory；一处 Trace 断言误把 finish 当作 ToolTrace。二者修正为现有接口，不放宽旧测试断言。自建代码格式问题经定向格式化修正。上述最终测试是在这些修正后执行；初轮错误记录仍保留在本轮 artifacts。

## 4. 真实数据库与迁移证据

- 已验证真实 PostgreSQL 数据库和账号身份，19 项真实数据库测试全部通过。
- `h2_0001 → p2_0001 → h2_0001 → p2_0001` 全部 exit 0；Alembic check 无额外差异。
- 对原 `research_runs/research_events/research_reports` 的列、索引、检查约束、外键及数据数量进行前后核对，结果一致。
- 没有修改原三表或迁移历史，没有访问现有开发库、agent_learning 或生产库。
- 容器 stop exit 0；Docker 自动删除完成后独立检查确认不存在。随机凭据文件及临时私密目录已删除。
- 初次 stop 后自动删除存在短暂延迟，未据此冒称清理完成，随后独立确认删除。
- 对应 `database-target.json`、`migration-verification.json`、`database-cleanup.json` 均在本轮证据目录；acceptance.json 收入脱敏目标、迁移及清理结果。

## 5. 文件保护

- 核对 823 个原 tracked 文件，只发生已列明三处非冻结增量接入变化。
- 398 个 B2/P1 历史产物哈希全部一致。
- Stage E 292 项：291 项与原历史哈希一致；README 是之前已授权的文档例外，本轮未修改。没有新增冻结差异。
- 原 13 个未跟踪文件逐项哈希一致，未暂存、未覆盖、未删除。
- .env.example、pyproject、requirements/锁文件、原 ORM、原迁移和冻结算法保持不变。

| 原未跟踪文件 | 前后 SHA256 | 结果 |
|---|---|---|
| `docs/diagnostics/quality_failures_2026_10_09/artifact_manifest.json` | `ee233e77df173293c437e887f78c141c6d36dcf264d6b004c0223853fd60f223` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/findings.json` | `f5590827026f11b87fbbc0729f0bbc2e6e26b4c1399af337980cf8c638cc93de` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/report.md` | `9579aabe3451eae73aa949beee92133103dbc0168ede980a0f0070d4851a099f` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-frozen-trainable-scope.md` | `abdec5284ce2de37d786cbd2bc66d8c49c23eb045fa1f9f64c8bdedb3b7c3f81` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-language-model.md` | `51c5dada6a6cdded716bd1a0bf670dee83a33e8f64558b03dfb321b60385508c` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-training-objective.md` | `b50a0e2ecc96c50e93b83e7d3bd0c2ccedb2aec1b48106539ff4bcee5bccbb29` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-vision-encoder.md` | `e62e2404f29e15cf4233a319dd69952ab0cbd28b73371ad74182e4e9260f797c` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-vision-language-bridge.md` | `cb0b57c84f80526ea0d89a90e2213fd261649f5ea3a57414efc732323fa48476` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-llava-frozen-trainable-scope.md` | `f1422c861901636998acd0eb45a379900e8fbe5f2bc8fb9a2db6e4290eaf5d09` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-llava-language-model.md` | `1156f7e4efc7ec8731be1e539753717b659aae1b76ebf398d7338fa2a219b936` | 一致 |
| `docs/diagnostics/quality_failures_2026_10_09/verification.json` | `f107553f46a803cd866a3d16a9892bdc5682bde958d2b4eecba98759a7f2fca3` | 一致 |
| `scripts/export_quality_failure_traces.py` | `b59cbb413bad0cf93936c28fd0896f2ba952aa532f6b4d945d87804f2185c23c` | 一致 |
| `tests/research_quality/test_failure_trace_export.py` | `9a84da8a40e69b3b834085fc4c2725de495dfb2893e04517d84c05cc169198bc` | 一致 |

完整机器证据：`artifacts/p2-agent-memory-2026-10-29/protection-verification.json`。13 项及 Stage E 例外的具体前后值也收入 [acceptance.json](acceptance.json)。

## 6. 本阶段文件

- `app/memory/__init__.py`
- `app/memory/config.py`
- `app/memory/errors.py`
- `app/memory/integration.py`
- `app/memory/models.py`
- `app/memory/policies.py`
- `app/memory/retrieval.py`
- `app/memory/service.py`
- `app/memory/tools.py`
- `app/persistence/memory_models.py`
- `app/persistence/memory_repository.py`
- `app/research/react_runtime.py`
- `app/workbench.py`
- `docs/acceptance/p2/acceptance.json`
- `docs/acceptance/p2/acceptance_report.md`
- `docs/acceptance/p2/audit-and-plan.md`
- `docs/architecture/agent-memory.md`
- `migrations/env.py`
- `migrations/versions/p2_0001_agent_memory.py`
- `tests/integration/test_memory_postgresql.py`
- `tests/test_agent_memory.py`
- `tests/unit/memory/__init__.py`
- `tests/unit/memory/fakes.py`
- `tests/unit/memory/test_memory.py`
- `tests/unit/memory/test_memory_schema.py`

## 7. 已知限制与未执行项目

- 仅宿主配置的项目级记忆，不提供用户身份、跨租户授权或数据库 RLS。
- 来源是本次用户原文及问题 hash。用户声明不等于独立事实核验；不会从论文、工具输出或模型总结自动提取记忆。
- 不同键的语义重复/冲突不自动识别；关键词检索可能漏掉改写表达。
- 软删除保留正文；敏感内容规则不是完整隐私方案。记忆参数最小化未改变原 Run 问题存储规则。
- 观察是快照，不提供多实例即时失效通知；没有新分布式调度、Redis 或无限重试。
- 未重新进行干净环境安装；没有新增依赖，迁移工具历史上缺少 migrations.lock 的限制仍存在。
- 当前用户开发库尚未执行 p2_0001，也未默认启用记忆。本轮仅授权并使用临时库；工程通过不表示实际开发库已启用。
- 没有执行新的真实模型自主记忆、完整研究或浏览器验收，不能据 Fake/Scripted 测试宣称模型质量提升。

## 8. Git 交付与停止边界

只显式暂存本阶段文件，不使用 add -A；保持原未跟踪材料和历史 artifacts。按数据与事务、工具接入、测试与文档拆分本地提交。最终 commit、push 退出码及远端分支 SHA 在用户交付索引中记录，避免证书自引用。不会自动 merge、创建 PR 或进入下一阶段。

工程范围 P2 达到验收条件。下一步可以另行准备少量 P1/P2 联合真实案例，但本次在文档、提交与授权 push 后停止。
