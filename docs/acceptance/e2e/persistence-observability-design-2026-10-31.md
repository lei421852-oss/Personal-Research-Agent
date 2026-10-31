# Stage 5.1：首次持久化故障诊断设计（待 Review）

日期：2026-10-31；本设计不是实现完成证明。

## 目标与信任边界

记录首次故障的可确认字段，输出到独立于故障数据库的结构化日志；不记录异常消息、SQL、参数、密钥、用户内容、论文文本或思维链。保留现有事务、预算、状态和 API 语义，不新增表或迁移。

## 事件 Schema v1

- event: 固定 persistence.first_failure。
- timestamp: UTC ISO 8601。
- run_id: 经严格 32 位十六进制验证；不可得时 null。
- correlation_id: 若无既有可靠 ID 则 null；禁止自行推断。
- operation: 白名单 append_events / save_report / save_paper_selection / queue_submit / barrier / close / unknown。
- event_type: 仅白名单 started / progress / completed / failed / skipped，其他 null；不记录事件内容或 node_id。
- write_stage: 精确已知值 queue / writer_apply / barrier / close / unknown；不能把未知阶段说成 commit 或 rollback。
- exception_type: 安全白名单中的异常类别；未知类统一 OtherError。
- internal_error_code: 项目错误码白名单或 null，外加分类 category。
- sqlstate: 仅接受严格正则 [0-9A-Z]{5} 的驱动提供值，否则 null；不输出驱动文本、约束文本、诊断字段或参数。
- process_id: 系统进程 ID；first_failure: true。

category 使用已确认信息：event_cursor_conflict；database_timeout；database_constraint_error（仅可验证 SQLSTATE 类别 23）；database_connection_error（仅可靠连接类/SQLSTATE 08）；transaction_error（仅可靠事务类别）；writer_internal_error；unknown_persistence_error。storage_unavailable 单独不足以判定连接或约束；未知分类保守保留。

## 锁存与并发

workbench 实际共享 latch。首次失败定义为同一 latch 周期的首次 fault，不得假设每个 run 各有一个。保持现有事件循环内同步 trip 行为；不将它宣传为跨线程或跨进程原子性。后续故障不能覆盖原 reason、诊断或重复刷日志。原先的 clear/reconcile 行为不变；记录周期可重新开始，不能自动清理故障或更改重试资格。队列满、屏障/关闭超时与 Writer 写入失败均需明确覆盖。

## 异常来源与 SQLSTATE

只在数据库异常尚可访问的边界提取五位安全 SQLSTATE 和固定异常分类；传递至既有 StorageError 的可选安全元数据，默认保持兼容。不能把数据库异常原始对象、__cause__、堆栈或消息存入诊断对象，也不更改原有 StorageError code、retryable、提交未知语义。捕获仅在实际可得时输出，取不到返回 null。

## 日志生命周期与失败策略

优先使用现有 Python logging，不将日志写数据库。默认 logging handler 可能同步阻塞或无进程外持久性：实现需采用有界、非阻塞的提交策略并明确队列溢出/日志落盘失败的可观察限制；诊断自身失败不能改变业务异常和事务结果。优先借用已有外部日志采集；不得承诺 SIGKILL、断电或存储失效时一定保存。正常与异常退出的进程外日志采用隔离进程测试证明。隐藏推理不记录。

## 测试与安全

隔离环境覆盖正常写入、游标冲突、瞬时错误、超时、提交失败/回滚、首次故障不可覆盖，检查 503、后续写入阻断、重复与资源释放。逐项区分真实数据库故障、模拟驱动故障及未覆盖。敏感字符串必须注入异常测试，验证日志与公共 API 不泄露；SQLSTATE 要有有效、无效和不可得测试。日志阻塞、输出失败、进程退出需明确测试。

## 待决冲突与环境

原计划 0.2 写“禁止执行数据库迁移或降级”，4.1 又写“验证目标后才执行迁移和测试”。按较严格约束目前不能迁移任何数据库；如要在新建、专用、可销毁的容器内执行既有迁移，需用户明确选择是否将该禁令限定于开发/生产库。即便批准，也需确认容器、端口、凭据、专用卷、目标及清理范围。不连接开发库、不更改遗留任务。

本设计等待 Review；未修改 Writer、数据库、研究运行或历史结果。
