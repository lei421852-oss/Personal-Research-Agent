# Stage 5.1：持久化链路代码审计

审计日期：2026-10-31；分支 feature/agent-memory；HEAD e7886aca1884848ff891c0843642ea1c77640a26。未连接开发数据库、未执行迁移或真实模型。

## 基线与保护

暂存区及已跟踪文件无差异；另有既有验收目录及 tests/test_stage2_gap_scenarios.py 未跟踪。历史清单原有 13 个未跟踪文件、398 个受保护文件重新计算 SHA256 全部匹配。保留所有材料。

## 调用链

1. ResearchRunManager.create 通过 RunPersistence.start → ResearchRepository.create_run 先提交 queued；成功后创建该 run 的 Writer 和执行任务。
2. ResearchRunManager._execute 先 mark_running，再运行 Runner；_sink 使用 call_soon_threadsafe 投递到事件循环；_append 以 run.events 长度分配 event_id（app/research/run_manager.py:229,245,274,570）。
3. RunPersistence.on_event 将事件构建为 AppendEvents，expected_last_seq=event_id-1，交给单 run RunWriter（app/persistence/lifecycle.py:123）。
4. RunWriter 的数据队列串行处理，每次 _execute 受 write_timeout、max_attempts 限制；RepositoryError 不重试，部分 StorageError 有界重试（app/persistence/writer.py:154,223,258,295）。
5. ResearchRepository.append_events 通过独立 Session 与 session.begin 开启短事务，对 run 行加锁、核对已有哈希及 expected_last_seq，事件与游标在事务内 flush；退出上下文提交（app/persistence/repository.py:589）。DatabaseResource.session 退出时 rollback/close 并屏蔽驱动异常原文（app/persistence/database.py:139）。
6. RunPersistence.finish 先 flush Writer，再构造终态和报告；TerminalCoordinator 按既有提交结果做只读核对（app/persistence/lifecycle.py:181）。

## 首次异常与锁存

- Writer._handle_data 异常处把 exc 存入 _failure，增加计数并 trip writer_write_failed:<code>；_failure 仅在进程内，进程退出后丢失。后续数据命令被丢弃，屏障报错。队列满、flush/close 超时也可触发 latch（writer.py:154,165,183,258）。
- PersistenceLatch.trip 只保留首个 reason，clear 受既有 reconcile 逻辑约束（coordinator.py:33）。单事件循环内 trip 不含 await；它不是跨线程、跨进程锁。
- 虽然类注释称每 run 独立，workbench.py:95 实际创建一个共享 latch 并传给 lifecycle/coordinator，多个 run 的 Writer 共享它。设计不得悄然改为每 run，诊断去重必须以共享锁存周期为准。
- callback deliver 中 finally 减计数但不捕获 _deliver/on_event 抛出的异常（run_manager.py:258）；后续 PersistenceLatched 可造成回调错误，不能将其直接当作首次写入异常。
- API 看到本实例 persistence_error 时返回 503 run_persistence_incomplete 与 reconcile_only（run_manager.py:896）；代码多个路径能进入同一公开错误，不能从 503 单独推断底层类型。

## 事务、错误和不确定性

- 每个 RunWriter 有串行队列，各数据库操作创建独立 Session；Session 失败时先清理、rollback/close，避免共用已失败 Session（database.py:139）。
- event 插入、last_event_seq 更新及 flush 在 session.begin 事务中；事务上下文退出时提交。已提交但确认丢失仍可能发生；已有批次一致性校验可处理整批相同事件重放，不能把未知提交当作已回滚。
- DatabaseResource 对 SQLAlchemy DBAPIError、连接错误等映射为 StorageError 并使用 from None；上层通用错误码会损失可安全持久化的底层细节。不能把所有 storage_unavailable 判为连接错误，约束/事务问题也可能被归一化。
- 应用记录 writer 关闭异常的 debug 日志，但首次 _handle_data 失败未输出结构化日志；日志系统未保证跨进程或断电持久化。现有 run_id 即可作为关联标识，不推测独立 correlation_id。

## 最小方案

在现有错误归一化边界提取并验证可用 SQLSTATE 和有限异常类别（不记录异常文本/SQL/参数），Writer 首次失败构造有界白名单诊断，随原有 latch 首次置位记录一次。队列满及屏障超时路径需覆盖；维持原错误传播、重试、状态机、schema 和 API。日志落 stderr 的进程外持久化由部署配置决定，不声称默认保证；不得让同步慢 handler 阻塞事件循环。详见对应设计文档。
