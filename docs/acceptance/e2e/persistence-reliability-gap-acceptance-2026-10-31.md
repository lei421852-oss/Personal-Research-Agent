# Stage 5.1.1：可靠性缺口验收

日期：2026-10-31；基线 106b30a。结论：核心离线修复**有条件通过**，Stage 5.2 **暂不能进入**：真实 PostgreSQL commit 失败尚未验证、最终版本尚无独立审查；历史故障根因仍未知。

## 验证结果

- 测试先行：新增用例在旧实现上 13 failed（Coordinator/重试/线程/计数及分类未实现），按计划保留对照事实。
- 实施后发现并修复竞争：消费者在 STOP 前意外退出时，旧版本 flush 返回成功但已接受事件残留。定向测试先失败，再修复并验证。
- 最终离线命令：env -u H2_TEST_PERSISTENCE_AUTHORIZED -u H2_TEST_PERSISTENCE_DATABASE_URL -u P2_TEST_MEMORY_AUTHORIZED -u P2_TEST_MEMORY_DATABASE_URL -u B1_TEST_ARXIV_AUTHORIZED -u B2_TEST_ARXIV_AUTHORIZED PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -p no:cacheprovider -q --tb=short tests/unit/persistence tests/unit/research tests/test_react_workbench.py。939 passed、1 warning、退出码 0，12.53 s。warning 为 Starlette TestClient/httpx 弃用警告。
- ruff check app tests migrations：通过；ruff format --check app tests migrations：295 files already formatted；git diff --check：通过。
- 真实隔离 PostgreSQL 16：用户单独授权仅网络故障测试，不创建业务表、不迁移。容器只绑定 127.0.0.1:55438，随机密码、专用匿名卷，已核对并销毁。暂停容器时健康检查 storage_health_timeout；恢复后连接成功；Fake Repository 使用真实 SELECT 1，Writer 输出 write_retry → write_recovered，latch 未置位，SQLSTATE null。证据 artifacts/stage511-network-20261031-164701/。该验证发生在最终日志 STOP 前异常退出修复之前，不能声称最终版本已重复真实数据库验证。
- 按本阶段禁止迁移/建表的约束，真实 PostgreSQL 事务提交失败及业务表部分提交测试为 BLOCKED；不能用 Fake 测试替代。未执行 test_end_to_end_postgresql.py。
- Python 子进程测试覆盖正常退出、sys.exit(7)、SIGTERM、SIGKILL；SIGTERM/SIGKILL 示例刻意让 handler 阻塞，验证无法保证留存，不证明部署系统的日志采集或 graceful shutdown。
- 首次异常、共享 latch、五个 Coordinator 原因、retry/recovered、重试耗尽、队列满、Handler 明确抛错、重启、并发 flush/emit、可用 SQLSTATE、脱敏由离线测试覆盖；外部 Handler 内部吞错不在可计数范围。

## 失败与证据

测试先行及发现的新竞争失败未作为最终通过结果；旧单元测试中原“只有一个总事件”断言按新契约改为严格检查一个 retry 加一个 first_failure，其余旧断言不弱化。网络探针仅 SELECT，Fake Repository 不验证真实业务事件表或事务 commit。无真实 LLM/VLM/arXiv 请求，无开发库写入。

## 保护与遗留

原 13 个未跟踪文件、398 个历史产物 SHA256 匹配；未修改迁移、数据库 Schema、Memory 或 Agent。当前自审不能称作独立 Review。需独立审查代码、补齐在另行授权隔离环境下的真实提交失败测试，并只读确认开发库遗留任务、实例和预算，再决定 Stage 5.2；不得自动重试真实任务。

## 本地 Git

代码和测试提交：a8a9e55。文档另行提交；按照本阶段要求不执行 push/pull/merge。
