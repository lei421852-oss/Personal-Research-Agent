# 聊天式研究工作台：最终验收报告

日期：2026-10-26。**前端工程与真实运行验收：PASS（带已知研究/语言限制）。** 本结论不表示模型研究质量全部通过，也不改变 B2 的 CONDITIONAL。

## 1. 基线与修改范围

- 初始分支：`feature/h2-2-research-persistence`。
- 初始 HEAD：`8ec79f613dcb2b688c6a0550ee904694bf60c1f2`；初始暂存区与已跟踪文件干净。
- 修改：`app/workbench_web/{index.html,style.css,app.js}`；新增 `adapters.js`。
- 新增：`tests/test_chat_workbench_adapters.py`、`scripts/check_chat_workbench_ui.cjs`、`scripts/accept_chat_workbench_real.cjs`。
- 文档：原位更新 `docs/guides/ui-quickstart.md`，新增本目录审计、验收 JSON 与报告。
- 没有修改 ReAct 主循环、Planner/Worker/Reviewer/Recovery/Synthesis、Retriever、API URL/DTO、ORM、Schema 定义、迁移文件或既有索引。
- 实现提交：`9b5d79c1b8dbb3bebbf92bc2939e843e99a5d016`。
- 没有执行远程 Git 操作。实施提交、文档提交、交付 HEAD 与文件哈希单独记录在本机 `artifacts/chat-workbench-2026-10-26/delivery.json`，避免提交文档自引用 SHA。

## 2. 界面与安全行为

左侧为新建、当前对话、本机历史、论文库和运行配置；中间直接回答优先，流程与详细报告可展开；右侧展示真实论文来源、引用快照与节点详情。使用黑白灰界面、少量状态色，支持窄屏。

- 中文输入：Enter 发送，Shift+Enter 换行，输入法组合事件不提交；运行中禁止重复发送，失败保留输入。
- 行动/Worker/审核节点只从现有公开事件形成；没有 Worker 事件就没有 Worker 分支。连线表达职责关系，不宣称并发时间。
- 事件按 ID 排序与去重，游标只推进连续前缀；明确截断时保留警告；缺口有界补读，不制造缺失步骤。
- API 适配保留错误类别、retry_mode、run_id，使用安全公开文案。UNKNOWN 不等于回滚，页面不自动再次创建。
- 原提交及幂等键保存在本浏览器；仅经用户点击，通过既有 Replay-Only 契约核对，不获得首次调度资格。
- 刷新、书签、`/?run=<id>` 和重连只读已提交任务。服务/数据库不可用不会清除书签或伪报没有记录。
- 常用 Markdown 只创建文本和安全元素；不执行模型 HTML、脚本或任意 URL。引用只连接报告实际暴露的来源，不新造证据。
- 取消调用既有 API；原节点没有取消事件时不推定它已取消。最终 report/answer/run 状态保持区分。

## 3. 实际启动与数据库

实际后端/前端均为 `http://127.0.0.1:8008/`，同源，无单独前端开发服务器。启动命令：

```bash
# 先在进程环境安全注入已授权本地开发库配置
python -m scripts.launch --host 127.0.0.1 --port 8008
```

本轮由受控启动进程读取被忽略的 `.env.ui-development`，使用上述同一入口；实际 PID/日志位于本机 `server-process.json` / `development-server.log`。服务已保留，方便用户直接体验。

用户指定已有 Windows PostgreSQL 的 localhost:5432，授权新建并保留 `personal_agent_ui_dev`。专用同名账号没有超级用户、建库或建角色权限。仅空的新库执行已有 `alembic upgrade head`（`h2_0001`），退出码 0。没有连接 `agent_learning` 或生产库，没有改动迁移源码。

WSL 最初被 pg_hba 拒绝；经用户明确批准，备份原配置后仅加入“本轮 WSL 单 IP + 新库 + 专用账号”的 SCRAM 规则，reload 成功、解析错误 0。未更改其他规则或防火墙。本机配置含秘密，均 Git 忽略、权限 0600，不在交付目录或提交中。

新库实测为 `research_runs=2`、`research_events=153`、`research_reports=2`、`alembic_version=1`。图形数据库客户端连接方法和只读 SQL 见[工作台指南](../../guides/ui-quickstart.md)。

## 4. 两次真实研究（不是模拟）

两次均使用原有 `deepseek-flash` 配置、现有语料/Embedding/索引；arXiv 显式禁用，VLM 调用为 0。最多授权 12 + 36 = 48 次模型调用；实测合计 **39 次模型调用、16 次工具调用**。每题仅一次创建，没有挑选重跑。

| 项目 | CLIP 简单问答 | BLIP-2 / Flamingo 协作研究 |
|---|---|---|
| run_id | `dafbac3f2fc945f3b6e0460bbaf2233a` | `1487522ff5624f0f8c2213568799b759` |
| 预算 | 12 | 36 |
| 实际模型调用 | 4 | 35 |
| 实际工具调用 | 1 | 15 |
| 实际耗时 | 23,971.8 ms | 115,908.4 ms |
| 实际路径 | 主 Agent + Evidence，线性 | 研究工具、Planner、2 Worker、审核、Recovery、Synthesis，协作树 |
| 公开事件 | 16 | 137 |
| 运行状态 | completed | completed |
| 答案状态 | answered | partial |
| 引用 | 4 | 11 |
| 研究覆盖 | 未经过多 Agent Reviewer | 14 需求，8 已审核、6 缺失；EvidenceCoverage=57.14% |
| 主 Agent 协议纠正 | 0 | 1 |
| Provider 重试 | 无额外记录 | 0 |
| 中文展示转换 | failed / invalid_report_translation | failed / invalid_report_translation |
| 浏览器脚本错误 | 0 | 0 |

CLIP 原始直接回答本来就是中文；协作原始直接回答也含中文。中文转换结构校验失败时，界面明确显示回退原文，没有冒充翻译成功。没有为此修改模型提示词或翻译模块。

协作题确实产生 BLIP-2 / Flamingo 两个 Worker；实际 Planner 生成 14 项需求，即使用户限定一个比较维度也不能把这些需求删掉来美化覆盖率。8 项已审核不等于每个直接回答句子都经过独立审核；最终主 Agent 的直接回答仍没有独立人工事实审核。本轮只核对公开引用与节点来源，不新做语义正确率结论。

## 5. 持久化、刷新与重启验证

两次真实运行各自页面刷新后均无新增 POST，状态/事件/报告均能读取。研究全部结束后停止本轮服务并重新启动，再用新浏览器上下文访问任务链接：新增研究 POST 为 0，Provider 调用为 0。

| 任务 | 重启后事件 | 报告 revision | 重启前后报告 SHA256 |
|---|---|---|---|
| CLIP | 16 | 1 | `c761c82163103f09faab66edb192a32f6112723e302b7dd0d7415aa5f723c254` |
| 协作研究 | 137 | 1 | `f5d787d7337c283d1ce59e11449042176ba0cf311adc548a876d7ad2a1e32b92` |

两个哈希前后相同。最终 DOM 4 / 40 个节点均能映射到各自原始 event.node_id；Worker 分支数分别 0 / 2。窄屏 body 无横向溢出；长流程在自身容器内横向滚动。

可直接只读打开：

- [工作台](http://127.0.0.1:8008/)
- [CLIP 已保存真实结果](http://127.0.0.1:8008/?run=dafbac3f2fc945f3b6e0460bbaf2233a)
- [双论文已保存协作结果](http://127.0.0.1:8008/?run=1487522ff5624f0f8c2213568799b759)

## 6. 工程测试与浏览器验收

本轮完整 pytest 是工作区口径，包含原有未跟踪诊断测试；这些诊断文件没有被加入提交。

本轮新增 12 项离线前端适配契约测试，63 项首批工作台/主协议定向测试通过。最终代码重新执行：

| 命令 | 本轮实际结果 | 退出码 |
|---|---|---|
| `python -m pytest -q` | 1623 passed、56 skipped、1 warning；57.48 s | 0 |
| `ruff check .` | All checks passed | 0 |
| `ruff format --check app tests migrations` | 255 files already formatted | 0 |
| `git diff --check` | 无空白错误 | 0 |
| Node `--check` | 4 个新增/修改 JS/CJS 文件语法通过 | 0 |
| 新浏览器回归入口 | 6 组受控场景通过，Provider 0 | 0 |

最终完整结果与退出码写入同目录 `acceptance.json`，完整原始日志保存在本机 artifacts；不引用旧 B2 数量冒充本轮测试。

浏览器受控回归是**真实页面 + 显式 API 夹具**，不是真实模型研究，Provider=0。通过 6 组场景：输入/提交/刷新与导航、Worker 布局、事件乱序与去重、UNKNOWN 与只读核对、取消、服务/数据库不可用。包含 XSS 文本、折叠和窄屏测试。

另有上节的两次真实浏览器研究与重启读取，未拦截研究 API。两类证据分别保存在 `browser-controlled/` 与 `real/`，不混作模型质量证据。

WSL Chromium 下载失败（连接被上游中断），不计为安装通过；实际使用本机已有 Windows Chromium 完成浏览器验收。未在干净机器重新安装全部依赖。

## 7. 完整性与 Git

Stage E 292 文件本轮零变化；原历史 README 文档例外继续保留，没有在本轮修改根 README。既有 `verify_release` 成功：291 项原哈希相同、1 项既有已授权 README 例外、47 项历史保护产物无变化。

原有 13 个未跟踪文件逐项 SHA256 一致；另核对 26 项不在跟踪源码中的历史保护材料无变化。没有修改 F2 / F3.1-E 原始产物与标签。候选提交秘密扫描未命中实际凭据，两个本机配置均被忽略。精确文件清单、差异、测试命令、日志哈希和提交记录随交付 JSON 保存；不执行远程 push/pull。

## 8. 限制、风险与阶段边界

1. 中文报告结构转换在两次真实运行中失败；回退原文有效。这是保留的研究/语言链问题，未在前端阶段修复。
2. 协作答案是 partial，存在 6 项缺失；不能把运行 completed、结构性引用校验、Reviewer 模型审核当成人工语义质量通过。
3. 本机历史仅为浏览器书签，没有新增数据库历史列表 API；新浏览器可用明确 run 链接读取已保存任务。
4. 本轮没有真实 VLM / 像素级视觉验证，也没有外部 arXiv 调用或 B2 故障复验；B2 仍 CONDITIONAL。
5. SQLite/Redis/分布式调度/自动接管/记忆系统均未加入。旧非终态任务仍按现有人工维护规则处理。
6. WSL IP 变化可能影响限定 pg_hba 规则；不自动扩大授权地址。当前环境是用户授权本地开发，不是生产部署验收。
7. 标准完整 pytest 中仍有显式门控跳过项；跳过不是通过。

本轮完成后停止，等待独立 Review，不自动扩展后端功能或再次启动模型实验。
