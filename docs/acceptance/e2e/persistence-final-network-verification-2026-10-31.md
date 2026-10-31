# 最终修复版本的 PostgreSQL 网络故障验证

2026-10-31，基线 HEAD `95175b1` 加上随后提交为 `bf3ac1c` 的相同工作树代码；运行 `python3 /tmp/codex-stage512-network.py`，退出码 0。隔离 PostgreSQL 16 仅绑定 127.0.0.1:55438，随机凭据与匿名卷，核验容器标签后暂停/恢复，完成后容器及专用卷已删除。无业务表、迁移、真实模型或 arXiv。

真实只读连接暂停时 `storage_health_timeout`，恢复后连接成功；使用真实 SELECT 1 的 Fake Repository 驱动 Writer，依次记录 `persistence.write_retry` 与 `persistence.write_recovered`，latch 未触发。探针退出码 0，约 5.53 秒。证据：`artifacts/stage512-network-20261031-195818/`。此测试不证明业务事件表提交、事件与报告原子性或生产负载安全。
