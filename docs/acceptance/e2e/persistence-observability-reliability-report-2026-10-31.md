# Stage 5.1：持久化可观测性与可靠性验收报告

日期：2026-10-31。基线：feature/agent-memory / e7886aca1884848ff891c0843642ea1c77640a26。

## 结论：CONDITIONAL PASS

首次 Writer 终态写入故障的结构化、脱敏诊断已实现；相关离线和隔离 PostgreSQL 回归通过。真实网络超时、强制终止下日志留存和真实提交失败的完整矩阵尚未通过验证，不能宣布完整 PASS。历史真实任务 49c8d48d69ff415e880e7f8fc0554124 的首次故障原因依然未知，不能把受控故障归因给历史案例。当前不应直接重新进入真实模型验收。

## 变更

- app/persistence/failure_diagnostics.py：白名单事件构建、SQLSTATE 安全提取、有界队列和后台日志线程；不依赖故障数据库。
- app/persistence/writer.py：队列满、屏障/关闭超时、最终写入失败时首先保持原有 latch，再尽力构建与投递诊断；重试耗尽时保留本操作首次失败用于诊断，原有最终锁存原因和重试行为不变。
- app/persistence/coordinator.py：共享 latch 增加首次安全诊断快照，不覆盖已经记录的诊断；clear 时同时清除快照。
- app/persistence/database.py、errors.py：原有 StorageError 码和 retryable 语义不变，增加可选安全 SQLSTATE 元数据，不携带驱动异常消息。
- tests/unit/persistence/test_failure_diagnostics.py、tests/integration/test_persistence_diagnostics_postgresql.py：诊断与隔离库测试。隔离库测试强制验证驱动、回环地址、端口、数据库、账号和 SSL 参数。

本阶段没有修改模型 schema、迁移脚本、Agent 或 Memory 业务逻辑；未对开发数据库执行任何操作。

## Schema 与日志边界

事件名 persistence.first_failure；字段 timestamp（UTC）、run_id、correlation_id、operation、event_type、write_stage、exception_type、internal_error_code、category、sqlstate、process_id、first_failure。run_id、枚举和 SQLSTATE 均经过白名单/格式限制；correlation_id 无可靠独立来源时为 null。日志不包含异常消息、SQL、参数、密码、连接串、论文或隐藏推理。写入事件循环只向容量为 128 的队列非阻塞投递，后台线程使用标准 logger；队列满或日志故障可丢失记录，进程外采集依赖部署，强杀/断电不可保证。诊断构造或投递异常不覆盖已有 latch。

实际运行中重试耗尽后才输出首次尝试的诊断；如果首次异常后重试成功，当前不会产生持久化首次故障事件。如果进程在重试完成前退出，首次异常也未保证留存。Coordinator 自身先锁存但没有 Writer 诊断的路径不属于本次覆盖。这些限制不能表述为首次异常 100% 持久化。

## 故障矩阵

| 场景 | 结果 | 证据边界 |
| --- | --- | --- |
| 正常事件写入 | PASS | 真实隔离 PostgreSQL，已有 Writer 与 Manager 测试。 |
| 游标冲突及锁存 | PASS | 真实隔离库，冲突类型和首次错误码检查。 |
| 瞬时数据库错误 | PARTIAL | Fake Repository 有界重试；重试耗尽后保留第一次故障的离线测试。恢复成功的瞬时错误不产生日志。 |
| 数据库连接超时 | PARTIAL | Fake pool timeout 类型映射及日志，未在真实隔离网络重现连接超时。 |
| 提交失败、rollback / 未知确认 | PARTIAL | 既有隔离库事务代理注入提交前/确认丢失，真实约束失败 23514 未留下部分事件；未完成全部真实 commit 异常路径。 |
| 后续异常不可覆盖 | PASS | 共享 latch 的离线场景验证首次 reason/诊断及单次投递。 |
| 进程退出日志 | PARTIAL | 子进程正常及 sys.exit(7) 验证；未验证 SIGKILL、断电、磁盘故障或外部日志采集。 |
| API 503 | PASS（指定案例） | 隔离库已有 Batch D 测试验证 storage_unavailable 和 report integrity 的 503；未证明每种 Writer 故障对应全部 API。 |
| 脱敏 | PASS（测试样本） | 注入 sk-private、password、secret 等异常内容，单元测试日志不含这些值；不是对所有部署日志的普遍保证。 |

## 执行结果与复核

- 定向离线命令：python -B -m pytest -p no:cacheprovider -q --tb=short tests/unit/persistence tests/test_react_workbench.py tests/test_agent_memory.py；取消数据库/arXiv 授权环境变量。202 passed，1 warning，退出码 0。
- 隔离 PostgreSQL：artifacts/stage51-isolated-20261031-161026/pytest.log；项目现有迁移仅在用户授权的一次性测试库执行，pytest 37 passed，1 warning，退出码 0；容器和匿名数据卷已销毁。包含真实约束错误 SQLSTATE 23514、回滚事件数 0、Writer、Manager、HTTP 故障测试。
- 初次隔离批次为 22 passed、1 failed：新增测试遗留测试记录与既有固定指纹冲突；保留 artifacts/stage51-isolated-20261031-160601/pytest.log。新增测试改为独立指纹和精确清理后，在全新隔离库回归通过；未改旧断言。
- ruff check .：通过；ruff format --check app tests migrations：294 files already formatted；git diff --check：通过。Starlette TestClient/httpx 弃用 warning 未处理。
- 原 13 个未跟踪文件、398 个历史保护产物 SHA256 全部匹配；schema、迁移和冻结配置无差异。未调用 Provider 或 arXiv。

## 继续前的工作

补齐真实隔离网络超时与提交失败矩阵、强制终止/外部采集边界和恢复成功重试的观测方案；评估有界日志队列满和处理器故障的告警。完成独立代码 Review，并按既定人工维护流程确认遗留任务和所有实例状态后，才单独授权真实模型单题冒烟。

## Git

诊断代码与测试本地提交：c8527dd。报告单独提交；不推送远程。
