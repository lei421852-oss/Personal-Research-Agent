# P2-DB 开发数据库接入验收

日期：2026-10-29。结论：**PASS（指定开发库的工程联动范围）**。

本轮将已实现的项目记忆接入指定开发库，并用受控数据验证现有工具、API 和界面。没有调用付费模型，也没有运行完整论文研究。

## 基线与目标

- 分支：`feature/agent-memory`；起始 HEAD：`21ee0e8e9bfd1673de9de80e67509217abe4ba16`。
- 原有 13 个未跟踪文件保留，Stage E 无新增冻结差异，398 个受保护历史产物哈希一致。
- 指定目标：PostgreSQL 18.6，`172.24.0.1:5432 / personal_agent_ui_dev`，当前 Schema `public`。不含认证信息。
- 应用使用 `H2_DATABASE_URL`；迁移进程使用单独注入的 `H2_MIGRATION_DATABASE_URL`，两个目标在执行前解析并核对一致。

## 迁移前备份

- 有效归档：`~/backups/personal_agent_ui_dev/personal_agent_ui_dev_pre_p2_memory_20261029_205932.dump`。
- 大小：153,959 字节；`pg_dump` exit 0；`pg_restore --list` exit 0，34 行归档目录。文件权限 0600，目录权限 0700。
- 归档可列出不等于已经完成还原演练；本轮没有创建还原库或执行恢复。
- 前两次备份失败：首次 WSL→Windows 环境变量传递方向设置错误；第二次 Windows 直连来源不满足现有 pg_hba 规则。两个零字节失败文件保留，不作为备份。
- 第三次使用与服务器版本一致的 PostgreSQL 18.6 Windows 客户端，经 WSL 本机临时转发连接同一目标，未改访问规则；转发已关闭。凭据只在受控子进程环境中传递，未写进命令参数或报告。

## 迁移与结构

- 迁移前重新核实数据库、Schema、`h2_0001` 和记忆表不存在。
- 实际执行 `.venv/bin/python -B -m alembic upgrade p2_0001`，exit 0；迁移后版本为 `p2_0001`。
- `public.agent_memories` 字段与模型字段一致；主键、唯一约束、检查约束、版本链外键和两个索引均存在。Alembic check exit 0，未发现待升级结构。
- `research_runs`、`research_events`、`research_reports` 保留。迁移前、迁移后和受控记忆测试后的三表行数及内容摘要一致；没有读取或展示业务记录正文。

## 开发配置与运行

只向 Git 忽略且权限为 0600 的 `.env.ui-development` 追加：

```text
AGENT_MEMORY_ENABLED=true
AGENT_MEMORY_PROJECT_ID=personal-agent
```

项目 ID 由用户确认。原数据库连接和其他内容保持不变。使用既有启动入口与安全 dotenv 解析，将已有 `H2_DATABASE_*` 和记忆设置注入启动进程；未创建新的数据库连接体系或启动脚本。既有手工启动说明见 [开发启动指南](../../guides/ui-quickstart.md)。

应用启动时的版本/表检查通过，工作台为 `http://127.0.0.1:8000/`；`/api/memories/stats` 返回 200。代码中的 MemoryRepository 与 ResearchRepository 使用同一个应用 DatabaseResource。

## 真实受控记忆联动

- 使用唯一逻辑键 `p2-db-integration-test-fe60064b9f39`，不包含真实用户敏感内容。
- 通过现有 Registry / ToolExecutor / MemoryBindings，在真实开发库执行 `memory_save → memory_update → memory_search`；新 Repository 可读取，其他项目范围不可读取。
- 本机 Memory API 列表及详情与同一记录一致；Chrome 实际打开工作台，显示更新后的内容、来源、状态和项目范围，返回后输入草稿保留。
- 随后通过 `memory_forget` 软删除测试的当前版本。默认有效列表中该测试记录数为 0。
- 审计历史保留一个 `superseded` 旧版本和一个 `deleted` 当前版本，均带测试标识；没有执行 SQL DELETE。测试记录 ID 和状态保存在本机忽略的本阶段证据中。
- 工具验证使用确定性调用；没有声称真实 LLM 已稳定自主选择记忆。

## 回归与静态检查

| 验证 | 结果 |
|---|---|
| 记忆 Service/工具、API 和 ReAct 定向测试 | 106 passed，exit 0；1 条既有 Starlette/httpx 弃用警告 |
| Chrome 既有离线界面回归 | 通过；网络由本地夹具拦截 |
| 真实开发库 API / 界面联动 | 通过；为本轮专用测试数据 |
| 数据库结构及 Alembic check | 通过 |
| Ruff | exit 0 |
| 格式检查 | exit 0，290 个文件格式符合 |
| git diff --check | exit 0 |

本轮没有重复运行完整测试集，未运行真实 arXiv、付费 LLM、完整论文研究、生产部署或数据库降级。隔离库测试的历史结果不冒充本轮开发库运行结果。

## 限制与保护

- 单项目 scope 并非多用户认证系统。
- `memory_forget` 是软删除，测试正文仍存在审计历史中；用户可在记忆页面选择全部状态查看。
- 应用配置文件含认证信息，已被 Git 忽略，不得提交或展示。
- 不将工作台对不受信任的网络开放。
- 本轮没有恢复测试，无法用 `pg_restore --list` 证明完整可恢复。
- 计划要求不自动 push 或合并；本地提交与远端操作结果由最终交付记录说明。
