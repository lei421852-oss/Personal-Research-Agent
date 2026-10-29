# 聊天式论文研究工作台：启动与使用

当前界面为左侧导航、中间聊天和横向执行流程、右侧论文证据。后端继续使用现有主 Agent 与可选多 Agent 研究链。

## 1. 环境与安装

Python ≥3.11。已有项目环境不要为了打开界面重复安装。新环境的基础依赖与模型/索引准备见[快速开始](quickstart.md)及[开发指南](development.md)：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,retrieval,vision,llm,data,api]'
```

没有独立的前端安装/构建步骤。HTML、CSS、JavaScript 由同一个 FastAPI 服务提供，不需要 npm、Vite 或 React。

可选持久化依赖必须显式安装：

```bash
python -m pip install --require-hashes -r requirements-runtime.lock -r requirements-persistence.lock
python -m pip install --no-deps -e .
python -m pip check
```

以上锁覆盖数据库基础设施；不替代真实检索、视觉或模型缓存准备。迁移安装与权限规则见[持久化指南](persistence.md)。本轮未在新环境重装依赖，不能将已有环境通过描述为重新完成干净安装验收。

## 2. 配置与启动

Provider 使用已有 `.env` 或进程环境中的 `LLM_API_KEY / LLM_MODEL / LLM_BASE_URL` 等字段。配置说明和本地 Embedding/索引要求见[快速开始](quickstart.md)。不要把文本模型配置当成视觉输入能力的证明。当前真实验收没有调用 VLM，没有重建索引或下载论文。

先检查，再启动：

```bash
python -m scripts.launch --check
python -m scripts.launch --host 127.0.0.1 --port 8008
```

前端和后端访问地址均为 **http://127.0.0.1:8008/**。无需第二个前端开发服务。

默认 `H2_DATABASE_ENABLED=false`，不连接数据库；已有归档回放仍可用。启用持久化必须安全注入现有配置。数据库设置不会主动读取 dotenv 文件；本机已经准备好被 Git 忽略的 `.env.ui-development` 时，可以在 WSL 仓库根目录使用：

```bash
set -a
. ./.env.ui-development
set +a
python -m scripts.launch --check
python -m scripts.launch --host 127.0.0.1 --port 8008
```

此本机文件含密码，不打印、截图或提交。新环境需自行创建获授权开发库、安装现有依赖，并按[持久化指南](persistence.md)使用独立迁移连接；应用启动不会自动迁移。不要对未知目标执行迁移。

前台启动用 Ctrl+C 关闭。终端保留启动日志；需要文件日志时可使用：

```bash
python -m scripts.launch --port 8008 > artifacts/workbench-local.log 2>&1
```

本轮后台体验实例的身份位于 `artifacts/chat-workbench-2026-10-26/server-process.json`。停止前先确认 `/proc/<pid>/cmdline` 确为本项目 `scripts.launch --port 8008`，只向该进程发送 SIGTERM，不终止其他服务。数据库保留，重启工作台不会自动接管旧实例的非终态任务。

## 3. 页面操作

- **新建研究**：只清空当前输入视图，不会删除数据库记录。运行中或提交结果未知时不能新建。
- **研究对话**：输入中文问题，Enter 发送、Shift+Enter 换行。输入法确认候选字不会触发发送。示例只预填，不自动执行。
- **模型调用上限**：使用现有请求的 `max_model_calls`，1–200；页面默认 36。后端是预算权威，达到上限可以部分结束或失败，不能保证完成研究。
- **历史记录**：仅列出本浏览器访问过的任务书签；点击后从服务端读取。归档回放明确标注，不调用模型。
- **任务链接**：`/?run=<run_id>` 可在新浏览器中只读打开已有任务。刷新、重连、点击书签都不会创建新任务。
- **论文库**：只读资源目录；只含元数据的论文不等于已有正文可供研究。无需手选论文，研究行动仍由主 Agent 决定。
- **运行配置**：显示既有服务就绪状态，不显示/修改密钥。
- **取消**：发送现有取消接口请求；只有后端确认取消后才显示任务取消。原节点没有取消事件时保留其最后真实状态，不伪造结束时间。

答案优先；执行过程、原始事件与详细报告可展开。点击节点查看公开工具事实；点击引用定位已有证据。常用 Markdown 结构安全排版，不执行模型 HTML。没有引用快照、作者或来源 URL 时明确缺失，不补写论文内容。

单 Agent 显示横向线性节点；实际调用研究工具后显示协作布局。论文 Worker 分支只在对应 Worker 事件到达后出现，支持折叠和横向滚动。职责连线不等于实际并发时间线。

## 4. 数据库里可以看什么

本轮使用用户已有 Windows PostgreSQL，专门创建以下本地开发目标：

| 图形客户端字段 | 值 |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `personal_agent_ui_dev` |
| User | `personal_agent_ui_dev` |
| Password | 从本机 `.env.ui-development` 中的 URL 密码部分读取；文档不保存 |
| SSL | 本机开发 `disable` |

可以使用已有的 DBeaver、DataGrip 或 pgAdmin。无需 Java 编程知识。WSL 应用连接 Windows 主机的地址可能不同，本机配置已使用经验证的地址；重启 WSL 后地址或单 IP 认证规则失效时先排查，不扩大到整个网络开放。

实际迁移创建 `research_runs`、`research_events`、`research_reports` 和 `alembic_version`。两次验收保存了 2 个任务、153 条事件、2 个报告版本；这是本轮快照，不是固定容量或未来保证。

可在新开发库执行以下只读查询观察关联：

```sql
SELECT run_id, status, effective_paper_ids, state_version,
       last_event_seq, current_report_revision
FROM research_runs ORDER BY created_at;

SELECT run_id, seq, node_type, node_id, status, source
FROM research_events ORDER BY run_id, seq;

SELECT run_id, revision, snapshot_kind, payload_sha256
FROM research_reports ORDER BY run_id, revision;
```

数据库记录、事件高水位与报告哈希是已提交事实；某些仅在内存计算的展示字段在重启后可能为空。`UNKNOWN` 不等于回滚，页面不会自动重排任务。所有人工维护继续遵守[持久化指南](persistence.md)的停机、核对、逐项授权要求。

## 5. 测试与真实调用的区别

离线回归与静态检查：

```bash
python -m pytest -q
ruff check .
ruff format --check app tests migrations
git diff --check
```

真实 PostgreSQL / Provider 集成测试仍有原有门控。未授权/未配置时 skip，不能视为真库/真实模型通过。前端适配单元测试使用 Node 或 ui-test 的 Playwright 自带 Node；不执行模型调用。

可选浏览器回归（先启动服务）：

```bash
python -m pip install -e '.[ui-test]'
python -m playwright install chromium
UI_NODE="$(python -c 'from pathlib import Path; import playwright; print(Path(playwright.__file__).parent / "driver" / "node")')"
UI_PLAYWRIGHT_PACKAGE="$(python -c 'from pathlib import Path; import playwright; print(Path(playwright.__file__).parent / "driver" / "package")')"
PLAYWRIGHT_NODE_PACKAGE="$UI_PLAYWRIGHT_PACKAGE" "$UI_NODE" scripts/check_chat_workbench_ui.cjs   --url http://127.0.0.1:8008 --output-dir artifacts/chat-browser-check
```

这个脚本使用真实页面和明确标记的本地 API 夹具，Provider 调用为 0。实际 Windows 浏览器可通过 `--browser-executable` 指定本机浏览器；路径应与脚本运行的系统匹配。本轮 Linux 浏览器下载失败，实际验收使用现有 Windows Chromium，没有将下载失败记为通过。

`scripts/accept_chat_workbench_real.cjs` 是独立真实调用入口，必须另行获得费用/预算授权并设置 `UI_REAL_RESEARCH_AUTHORIZED=yes`。`--help` 说明 simple/collaboration 案例。每个案例先记录提交尝试，再只提交一次；已存在的尝试不会自动重跑。固定案例只作工程验证，不代表模型质量基准。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| 页面可开但不能发送 | 运行配置中的 main_agent_ready、模型/语料/索引，不重建索引绕过问题 |
| 提交结果未知 | 保留问题、幂等键和原请求，仅点击“只读核对原提交”；不重发新任务 |
| 服务中断 / 数据库不可用 | 保留已有内容与书签；点击顶部连接状态只读刷新，不清空历史 |
| 事件缺口 | 从连续游标补读；多次仍不齐会提示暂停，不补画节点 |
| 没有 Worker 分支 | 本次没有产生 Worker 事件，不能用问题关键词猜测协作 |
| 中文转换失败 | 展示权威原文并标明原因，不伪造中文报告；原回答也可能本来就是中文 |
| 研究 completed 但部分回答 | 执行终态不等于证据齐全；查看 missing_requirements 和 answer_status |
| 重启后旧任务不可执行 | 不自动接管；终态已提交记录仍可只读查看 |

B2 外部 arXiv 工具仍为 **CONDITIONAL**。本轮明确禁用 arXiv，不对超时/空结果问题复验或改写结论。当前真实多 Agent 案例的 8/14 已审核覆盖和中文转换失败见[本轮验收](../acceptance/chat_workbench_2026_10_26/acceptance_report.md)。
