# Stage 5.1.4：隔离 PostgreSQL 故障矩阵

最终提交 `6436f1427c4315868118624f8f1d8aeea369acb7`；执行 `STAGE514_ISOLATED_TEST_AUTHORIZED=yes .venv/bin/python -B scripts/verify_stage514_postgresql.py`，退出码 0。用户单独授权一次性 PostgreSQL 16、本机端口、随机凭据、专用卷、既有迁移及合成数据；容器身份已核对并在完成后销毁专用容器和卷。证据：`artifacts/stage514-business-20261031-204439/`。未连接开发库执行写入。

| 场景 | 观察 | 边界 |
| --- | --- | --- |
| COMMIT 请求未转发 | 事件 0、水位 0、Writer 重试 0、latch 置位 | 代理在请求到达服务器前断开；不是处理期间断线 |
| 服务器提交、确认丢失：事件 | 独立读取事件 1、水位 1，Writer 重试 0、latch 置位 | 仅本次合成写入 |
| 服务器提交、确认丢失：终态 | 返回 UNKNOWN；独立读取 completed 和 1 报告；token/摘要只读 reconcile confirmed | 本场景尾事件为空 |
| UNKNOWN 后不同尾事件 | A 请求未送达，B 同 token/摘要提交不同尾事件；A reconcile content_conflict，latch 保留 | 非空尾事件场景 |
| 不同尾事件并发 finalize | new + content_conflict，事件和报告各 1 | 两个 Repository 并发，非分布式生产负载 |
| 同身份同事件并发 finalize | new + already_committed，事件和报告各 1 | 非多进程故障恢复 |

真实 PostgreSQL 23503 单次拒绝、回滚和 latch 证据沿用阶段 5.1.3：`artifacts/stage513-constraint-20261031-202049/`。COMMIT 在服务器处理过程中断线的精确时序、两个真实 Worker 自动恢复与进程崩溃未验证；不把代理截断请求或确认当成该场景。
