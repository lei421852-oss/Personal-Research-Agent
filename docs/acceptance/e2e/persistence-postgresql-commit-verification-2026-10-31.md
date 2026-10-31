# PostgreSQL 提交故障验证

2026-10-31，基线 HEAD `95175b1` 加上随后提交为 `bf3ac1c` 的相同工作树代码；运行 `python3 /tmp/codex-stage512-commit.py`，退出码 0。使用单独的一次性 PostgreSQL 16、仅本机端口和专用匿名卷；执行测试专用 TEMP 表、合成数据及延迟外键约束，未运行项目迁移或访问开发库，完成后删除容器及其专用卷。

PostgreSQL 在真实提交时以 SQLSTATE `23503` 拒绝事务；记录 `database_constraint_error` 和 `first_failure`，latch 置位，回滚后两个测试表记录均为 0，新 Session 的 SELECT 1 返回 1，活动租约为 0。内部错误仍为 `storage_unavailable`，现有分类将约束错误视为可重试，因此实际提交尝试为 2 次，并非理想的不可重试分类。探针退出码 0，约 0.46 秒。证据：`artifacts/stage512-commit-20261031-195834/`。

证据只涵盖临时表同一事务，不证明真实 Run/Event/Report 的原子性。尚未控制服务端提交成功但客户端未收到确认的真实网络时序；UNKNOWN 场景为 PARTIAL，不能以暂停数据库或本次明确拒绝代替。无依据声称已验证未知提交下的完整幂等性或没有重复写入风险。
