# H2.2阶段1：实现与验收报告

日期：2026-10-24。结论：**离线工程验收通过；真实PostgreSQL集成待目标授权，整体标记CONDITIONAL。**

## 实现范围

实现可选数据库配置、安全URL解析、AsyncEngine与async_sessionmaker、独立Session作用域、工作台lifespan资源管理、GET /api/storage/health，以及依赖锁/指南/测试。默认禁用不导入SQLAlchemy/asyncpg、不读取DB URL、不创建连接池；原Manager、研究路由与研究链未改。

唯一修改的原生产文件为app/workbench.py。新增app/persistence中的__init__/config/errors/database/dependencies和app/api/storage.py；新增5份依赖输入/锁、docs/guides/persistence.md、新单元测试目录及1个默认跳过的临时PostgreSQL集成测试。完整20文件清单与SHA在acceptance.json.changed_files。

## 本轮实际结果

| 检查 | 实测结果 |
|---|---|
| 完整pytest最终代码 | 1317 passed, 1 skipped, 1 warning in 32.67s；exit 0 |
| 新增离线单元测试 | 52 passed，已包含在完整结果中，不重复相加 |
| 干净锁环境新旧相关测试 | 77 passed, 1 skipped, 1 warning in 3.56s；exit 0 |
| 无SQLAlchemy/asyncpg环境的默认禁用回放 | 1 passed, 1 warning in 1.23s；exit 0 |
| 两个干净环境pip check | 均exit 0 |
| Ruff | 全范围check通过，exit 0 |
| 格式 | 280文件符合格式，exit 0 |
| git diff --check | exit 0 |
| PostgreSQL临时测试 | 1 skipped：未获得目标授权，无连接尝试 |

完整回归复用原.venv的研究依赖，pytest/Ruff来自独立regression-tools环境；原.venv未安装或升级依赖。干净环境基于三份hash锁安装，独立禁用环境只安装runtime/test锁，并实际确认SQLAlchemy和asyncpg不存在。两个验证口径分别报告，不将其混成一次“全套干净安装研究实验”。

## 生命周期与安全验证

覆盖启动时Manager构造失败、Session工厂失败、Manager关闭异常仍清理DB、不同lifespan独立Engine、关闭开始立即拒绝新Session、请求取消/清理中取消、清理超时、Session rollback/close及不自动commit。数据库HTTP错误仅返回固定代码与说明，测试植入虚构连接串/密码验证无泄露。真实Engine仅验证惰性构造和无连接Session；连接健康测试使用Fake。

健康检查成功仅表示显式SELECT 1成功，scope=connection_only；不表示Schema、权限、迁移或业务持久化可用。现有研究health和launch --check保持原语义。

## 实际发现及处理

第一次干净相关测试为4 failed、72 passed、1 skipped，原因是原Prepared Corpus测试需要PyMuPDF，而最小api/llm锁不包含data extra。将原pyproject已声明的PyMuPDF范围补入独立测试输入/锁后重跑通过，未改冻结pyproject、语料或测试预期；首次失败原日志保留在initial_clean_failure.txt.gz（gzip原始日志）。

哈希锁首次解析需获取跨平台包hash，分别约221秒、235秒、109秒；PDF补充约143秒。工具会话刷新中断过一轮未完成工作，已保留未完成日志并重跑到明确退出码，不把截断日志作为通过。版本解析/下载耗时不是运行时数据库或Agent耗时。锁不提交个人包源、凭据或机器绝对路径。

## 保护与Git

原基线：release/v1.0.0 @ d7777cbde96d46285de6be6cb9d58c13e6d72a67。实施分支：feature/h2-2-stage1-database-infrastructure。实现提交：e4899dcd40d914205ecb6adf7f10a3a94ad47c44。

原730项比对只有已批准app/workbench.py变化，其余729项不变；13个用户未跟踪文件逐项SHA一致且未暂存。Stage E 291项原SHA一致，README保留授权例外；47个受保护产物、34个F3.1-E产物一致。未新增业务ORM/迁移，未操作远程Git。

实现diff：20 files changed, 2036 insertions(+), 19 deletions(-)。精确补丁见implementation.patch.gz（gzip原始补丁）；本验收记录在后续独立文档提交中保存，不扩大生产修改范围。

## 未验证与停止边界

- 未授权临时PostgreSQL目标，因此没有验证真实认证、TLS、实际网络/连接池故障；不能宣称PostgreSQL集成验收通过。
- 保留1条既有Starlette/httpx弃用警告，不在本阶段迁移httpx2。
- 干净环境使用新锁定版本；完整研究套件验证在原依赖基线上完成，未运行真实Provider/研究或重建索引。
- 清理预算超限会明确报storage_cleanup_failed，不能保证强制关闭任意不响应取消的驱动。
- Run/Event/Report仍在原内存管理器中，本阶段没有持久化或自动恢复。

**阶段1实施停止。下一步仅在维护者明确授权临时测试目标后补连接集成；不自动进入阶段2，不执行生产DDL/DML或Alembic迁移。**
