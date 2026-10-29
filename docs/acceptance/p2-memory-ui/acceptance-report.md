# P2 记忆展示界面验收

日期：2026-10-29。结论：**本地工程验收 PASS**，真实模型自主使用记忆的表现不属于本轮评测。

## 基线与范围

基线分支 `feature/agent-memory`，提交 `ec1fcc512a29048a57da1bf0919b4705622bc94a`。复用 P2 已有记忆模型、迁移、Service、四种 Agent 工具和证据边界；只增加项目范围限定的只读查询、API 和工作台独立视图。原主页面只增加“记忆”入口，返回复用同一个页面，保持草稿与任务轮询。

项目无多用户身份认证，当前限制是宿主配置的单项目 scope。接口不接受客户端指定 scope，跨项目 ID 返回 404；这不能作为跨租户认证或 RLS 的证明。

## 接口与页面

- `GET /api/memories`：关键词、类型、状态、页码与页大小；默认有效记忆，`status=all` 可查看历史。
- `GET /api/memories/stats`：当前项目范围内各状态统计。
- `GET /api/memories/{memory_id}`：完整内容、类型、来源、版本、状态、时间及替代关系。
- 到期状态基于当前时间推导；有界 offset 分页按更新时间及 ID 稳定排序，不保证并发写入期间跨请求快照。
- 无记忆是正常空结果；未启用和存储不可用返回 503；错误和空状态分开展示，统计失败不遮盖可用列表。页面支持刷新、搜索、筛选、分页和详情，无编辑或删除入口。

详见 [使用与 API 文档](../../guides/memory-workbench.md) 与 [记忆架构](../../architecture/agent-memory.md)。

## 实际验证

| 验证 | 结果 |
|---|---|
| 完整离线 pytest | 1769 passed、78 skipped、1 条既有 Starlette/httpx 弃用警告，exit 0 |
| 独立 PostgreSQL：原记忆事务 + 新增 API 测试 | 22 passed，exit 0；其中 3 项为新增 API 真实数据库测试 |
| 新增只读 API 和已有记忆离线定向测试 | 68 passed，exit 0 |
| Windows Chrome 离线浏览器 | 导航、草稿、任务轮询、列表、详情、分页、空状态、部分失败与错误通过；所有网络由测试夹具拦截 |
| Windows Chrome + 真实隔离数据库 | 实际工作台页面通过实际 API 读取临时库中的测试记录和来源，返回后草稿保留 |
| 视觉对比 | 1440×900 前后截图及主聊天区、输入框、右侧区域坐标核对通过；导航仅新增记忆按钮 |
| Ruff / formatter / git diff --check | 全部 exit 0，格式检查 290 个文件 |

真实浏览器数据库联动使用专用测试数据和临时 manager 桩；它验证记忆页面、API 和数据库链路，**不代表完整研究任务或真实 Provider 已执行**。该桩没有实现研究健康检查/回放的所有接口，非记忆接口可能返回 500；相关主页面与研究事件行为另由拦截夹具浏览器测试及既有回归验证。

完整离线回归中，78 项因网络或数据库授权门控而跳过；其中新增的 3 项数据库测试已在独立授权进程中真实执行。不能将跳过项当成真实通过。

实际主要命令：

```bash
.venv/bin/python -B artifacts/p2-memory-ui-2026-10-29/run_offline_checks.py full
.venv/bin/python -B -m pytest -q tests/integration/test_memory_postgresql.py tests/integration/test_memory_ui_postgresql.py
.venv/bin/ruff check .
.venv/bin/ruff format --check app tests migrations
git diff --check
```

真实 PostgreSQL 测试仅在显式门控和已授权目标下运行；完整目标校验和临时密码由本机测试启动器处理。Windows 浏览器测试命令与结果见本轮结构化证据，不需要付费模型。

## 隔离与保护

用户明确授权使用一次性 PostgreSQL 16、127.0.0.1:55437、专用测试库与随机密码；测试迁移、记忆数据和查询仅发生在该库。验收后容器及私密凭据均已删除；未访问或迁移用户开发库、agent_learning 或生产库。

13 个既有未跟踪文件、398 个 B2/P1 保护产物、52 个 P2 原始验收产物哈希不变。Stage E 共 292 项，291 项与原冻结哈希一致，README 为此前授权的文档例外且本轮未改；没有新增冻结差异。

本轮未请求真实 arXiv、未调用付费 LLM，也未开始完整论文研究。用户的开发数据库尚未启用此功能；如需启用，须另行按已有迁移与数据库授权流程操作。

## Git 与后续

工作按后端、界面与测试、文档分批提交到 `feature/agent-memory`，完成后按用户约定推送 `research-agent`；不自动合并。具体提交和远端 SHA 记录在最终交付证据中，避免文档对自身 commit 产生循环引用。
