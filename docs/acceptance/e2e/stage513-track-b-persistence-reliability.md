# Stage 5.1.3 Track B：持久化可靠性

结论：**CONDITIONAL PASS**。已修复确定性约束错误重试及普通 Writer 未知提交重放，完成隔离 PostgreSQL 的真实提交拒绝和提交确认丢失验证；提交未完成时断线、完整业务组合及生产负载尚未全部验证。

## 策略及修改

代码提交 `9d821714a4d8ccd849645881e6c88400d13a5273`。`DatabaseResource.session` 在 Session 清理成功后，仅将连接池获取超时、PostgreSQL 明确回滚的 `40001`/`40P01` 标为可安全完整重放；`23xxx`、未知连接错误及清理失败默认不重放。Writer 要求 retryable、安全重放标记与可识别原因同时成立；写入整体超时直接锁存。既有错误代码、次数和事务边界保持；收敛的策略会牺牲部分不明连接故障下的自动恢复。连接池超时安全判断依赖当前单 Session、单连接事务实现。

修改持久化错误、数据库资源、Writer、相关单元测试，以及 `scripts/verify_stage513_postgresql.py`、`scripts/stage513_business_probe.py`。隔离脚本要求 `STAGE513_ISOLATED_TEST_AUTHORIZED=yes`、固定回环测试目标、随机命名容器和标签，并核查实际端口；拒绝优化模式，失败或无法确认清理返回非零。后续运行前必须重新取得适用的隔离环境授权。

## 验证

- 离线相关回归：963 passed、1 warning、退出码 0；Ruff 与格式检查通过（35 文件），`git diff --check` 通过。包含 23502/23503/23505/23514、40001/40P01、安全清理、未知连接、Writer 超时及脚本安全门控。旧模拟测试已改为明确表示可安全重放的瞬时错误，并保留首次诊断断言。
- 真实 23503：专用 PostgreSQL 16 中延迟外键拒绝 COMMIT，Writer 仅一次尝试，测试行数 0，锁存与首个故障诊断存在；证据 `artifacts/stage513-constraint-20261031-202049/`。该项使用测试专用表和替身 Repository，不代表业务端到端。
- 真实提交确认丢失：隔离 PostgreSQL 16 完成既有迁移后，使用本地 TCP 代理在数据库返回 COMMIT 完成消息时丢弃确认。真实 Repository 的事件写入后，独立连接查到 1 条事件和水位 1，Writer 重试 0、锁存；终态写入后返回 UNKNOWN，独立读取为 completed 且报告 1 条，基于原 token/哈希只读 reconcile 为 confirmed。最终安全脚本的证据 `artifacts/stage513-business-20261031-202619/`。两套独立容器与专用卷均已销毁；仅在隔离库执行迁移与合成数据写入。
- 初始确认丢失探针也有测试专用表验证 `artifacts/stage513-unknown-20261031-202129/`；不能将该替身测试称为业务 Repository 验证。
- 独立复核发现 probe 授权/目标校验缺口，已增加独立授权门控、端口映射校验及优化模式拒绝，复核确认该 Major 已解决；策略实现未发现其他已确认 Critical/Major。

确认丢失验证覆盖特定事件、终态与报告组合，不能推断所有普通报告写入或所有并发时序均得到证明；COMMIT 未完成即断线未进行可靠注入。普通 Writer 在不明结果时保守锁存，未实现新的自动 reconcile 或自动重放。
