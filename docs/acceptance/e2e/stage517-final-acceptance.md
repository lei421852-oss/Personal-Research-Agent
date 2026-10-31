# Stage 5.1.7：执行一致性与任务所有权验收

日期：2026-11-01。范围为隔离测试与代码审查，不代表生产部署验收。

## 实现与边界

- 数据库确认 UPDATED 后才允许 Runner 执行；不存在、版本/状态/所有权冲突或异常均不执行，也不替其他执行者提交终态。
- 状态、事件、报告和终态写入在数据库行锁内核对 owner；只读终态核对继续独立。直接调用 Repository 写入接口的调用方需传入正确的 expected_owner_id，未提供时安全拒绝。
- 排队但尚未领取的任务取消只能对仍为 queued 的记录收尾，并使用版本 CAS；同一 lifecycle 的领取与 Writer 清理由按 run 的进程内锁协调。
- 重启后未附着的非终态任务保持待人工核对，不自动接管、重跑或修改历史任务。本阶段未接触开发库，不能据此宣称重新核验了历史任务当前值。
- 不新增数据库字段或迁移；owner 标识不是跨进程租约、心跳或 fencing token，也不能证明历史执行者存活。

## 验证证据

- 修复前执行资格测试：非 UPDATED 仍运行和错误终态路径有可复现失败。独立复审另以同步屏障复现共享 Writer 竞态；新增测试在修复前失败。
- 离线回归：tests/unit/persistence、tests/unit/research、tests/test_react_workbench.py、tests/test_agent_memory.py，1008 passed、1 warning，退出码 0；不含真实模型、arXiv 或真实数据库。警告为 Starlette TestClient 弃用提示。
- 一次性 PostgreSQL 16：在独立测试容器先升级到 h2_0001 运行既有数据库测试，20 passed；再升级至 head 运行合成探针，确认错误 owner 拒绝状态、事件、报告和终态写入；两个竞争执行者仅一个调用 Runner；未启动即取消不覆盖正在运行任务；新 Manager 不接管非终态；正常生命周期事件和终态持久化成功。容器和专用匿名卷已销毁。
- 证据：artifacts/stage517-ownership-20261101-010922/result.json、commit_probe.log、existing_postgresql_regression.log；该目录按项目规则忽略，不属于 Git 提交。
- 早期一次数据库回归由于测试新增参数顺序错误而收集失败；修复后另一次由于新探针残留记录与旧测试 fixture 冲突且旧测试固定迁移版本而失败。均保留在 artifacts/stage517-ownership-20261101-010456/、artifacts/stage517-ownership-20261101-010530/；随后使用干净目标、正确迁移顺序重跑通过。
- 范围内 Ruff check 和 format check、git diff --check 通过。未执行完整工程全部测试。
- 独立代码复审：先后发现未启动取消和共享 Writer 竞态；修复后的屏障测试通过，复审未发现新的已证实 Critical/Major；不等同穷举全部跨进程故障。

## 保护与限制

起始分支 feature/agent-memory，HEAD 5dce6c0aecc0577acf0b092d285f85e5b0d785f0。原有 13 项未跟踪材料及 398 项历史保护材料与既有清单哈希一致；Stage E 冻结清单 292 项中仅根 README 与历史 SHA 不同，该文档例外已由用户此前授权；本阶段未改 Schema、迁移和 README。

没有连接开发数据库或历史任务、没有调用 LLM/VLM 或 arXiv。未引入自动恢复；数据库故障、进程崩溃及跨实例执行所需的租约/隔离令牌仍不在本阶段范围。Stage 5.1.7 通过本次约定范围的工程验证，后续上线或接管历史任务须单独评估。
