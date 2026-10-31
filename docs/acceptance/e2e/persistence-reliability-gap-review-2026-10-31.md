# Stage 5.1.1：持久化可靠性缺口代码 Review

日期：2026-10-31；审查基线 feature/agent-memory、HEAD 106b30a；代码提交 c8527dd。此为当前实现者的静态复核，不能冒充由独立人员或独立工具完成的 Review。

## 已确认问题

1. Coordinator 的 finalize_cancelled、finalize_outcome_unknown、finalize_content_conflict、finalize_pending_review 和 finalize_reconcile_unavailable 直接 latch.trip，不构造结构化诊断（app/persistence/coordinator.py）。
2. Writer 的 _execute 将第一次异常暂存、计数并重试，但恢复成功时没有事件；进程在重试中退出也可能无法留下第一条异常（app/persistence/writer.py）。
3. emit_failure 与 flush_diagnostics 对 Worker 状态和队列投递不共用同步边界；STOP 后仍可能插入事件，且并发 flush 可插入重复 STOP（app/persistence/failure_diagnostics.py）。
4. queue.Full、worker 启动异常和 LOGGER.error 抛出的异常只返回 False 或忽略，没有独立计数；标准 logging handler 自行吞掉的异常外层不一定可观察。
5. Writer 自身 write_timeout 被包装为 WriterFailed，当前分类可能当成 writer_internal_error；writer barrier/close 超时当前笼统分入 database_timeout，语义需区分。
6. SQLSTATE 经 database.py → safe_database_metadata → StorageError 保留，已有 PostgreSQL 23514 测试；未证实普遍覆盖所有驱动与事务阶段。

## 保持不变

保留原有锁存原因、重试预算、事务与公开 API；保持队列有界，事件循环不等待日志处理线程。原有 13 个未跟踪文件和 398 个历史保护产物不得修改。历史真实故障根因仍未知，不将受控故障当成历史根因。

## 真实数据库安全边界

本计划同时要求禁止迁移与隔离 PostgreSQL 故障注入。目前没有现成确认可销毁、已迁移且获本次授权的隔离库，因此真实数据库测试先标 BLOCKED；不接触开发库、不复用其他项目容器、不执行迁移。继续本地离线测试。
