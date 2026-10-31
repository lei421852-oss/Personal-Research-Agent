# Stage 5.1.1：持久化诊断缺口修复说明

基线：feature/agent-memory，106b30a。日期：2026-10-31。

## 变更范围

- app/persistence/coordinator.py：五个现有锁存入口统一先获取首次 latch，再构造白名单诊断。诊断失败只增加独立计数，不覆盖原因或改变 UNKNOWN/只读核对流程。
- app/persistence/writer.py：沿现有重试预算记录 persistence.write_retry；重试后成功记录 persistence.write_recovered；重试耗尽仍由原有路径记录 persistence.first_failure 并使用原 latch reason。
- app/persistence/failure_diagnostics.py：有界 128 项队列；RUNNING/STOPPING/STOPPED 共用短锁，STOP 后不再接受同代事件；并发 flush 只放一个 STOP，join 不持锁。若线程未消费 STOP 就退出，flush 在时间预算内重启消费者并排空已接收事件。日志 handler 不在事件循环执行。
- tests/unit/persistence/test_reliability_gap.py：Coordinator、重试恢复、队列溢出、计数、并发、线程异常、退出和超时分类离线测试。
- tests/unit/persistence/test_failure_diagnostics.py：将旧的单事件数量断言改为严格要求 write_retry + 唯一 first_failure；不是移除失败约束。

## 事件、计数与限制

write_retry 包含安全字段、attempt/max_attempts；write_recovered 包含 failed_attempts/recovered_attempt。SQLSTATE 仅保留严格格式，原始异常字符串不记录。writer_timeout 与 database_timeout 分开。四个只读内存计数器：queue_dropped_total、emit_errors_total、handler_errors_total、worker_restarts_total；计数器随进程重启归零。

handler_errors_total 仅统计能够在 LOGGER.error 边界检测到的异常，无法统计第三方 Handler 内部调用 handleError 后自行吞掉的错误。队列满或 STOPPING 拒绝新事件并计数；非阻塞不等于不丢日志。标准输出必须由部署侧采集；SIGTERM 默认终止和 SIGKILL、进程/主机故障时没有可靠落盘保证。

并发 Writer/Coordinator 共同锁存是现有单事件循环内设计；不宣称跨线程、多进程 latch 安全。使用多线程保护诊断队列本身，不变更原持久化事务、运行状态和调用预算。
