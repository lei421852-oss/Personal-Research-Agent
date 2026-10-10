# Stage G4.2 · ReAct 主 Agent 编排验收

结论：**工程验收 PASS**。默认研究入口已切换为问题驱动的主 Agent；多论文研究是可选工具。验收范围是协议、领域连线、共享预算、输出适配和界面，不是新增真实模型质量 Benchmark。旧索引字节清单的两项差异单独保留说明，不把它们计为完全相等。

## 实际交付

- `react_policy.py`：新主入口策略、扩展 finish 契约；执行方法仍复用冻结 `BoundedToolAgent.run`，没有复制或重写其循环。
- `research_handoff.py / research_tool.py`：有来源的 1–3 篇论文交接与单次可选长研究工具。
- `react_budget.py / react_runtime.py`：原同步循环与既有异步研究链的薄桥接、总请求/工具账本、共享 ResourceGate、取消收尾和实际角色事件。
- `discovery_resources.py`：复用已有增强画像缓存，逐条验证 Catalog 与 Paper Index；不抽取新画像、不重建索引。
- `answer_first.py`：直接回答、关键依据、引用、真实缺口与原报告的显式 DTO 映射。
- 原 `ReportLanguageAdapter` 的非冻结展示层增加直接回答与关键依据的中文字段；保留英文原文、引用和状态。
- 默认 `app.workbench` 接入新运行时；旧 `app.main`、旧内部研究入口保持兼容。API 可选范围接受 1–3 篇，UI 只输入问题，25 篇论文只供资源浏览。
- 三栏界面去掉论文勾选，实际事件驱动节点，避免重复主 Agent 或残留运行节点；首屏可见开始按钮和直接回答，长引用折叠，提供原始证据查看。

源码和模块职责见 [架构说明](../../architecture/react-workbench.md)，准确启动与检查命令见 [使用指南](../../guides/react-workbench.md)。

## 三条路径的工程演示

以下全部为 **Scripted 策略 + 真实领域组件 + Fake 检索/Provider**。用于证明工具反馈和运行连通性，不能推导真实模型路由或论文质量。

| 路径 | 实际动作 | 主决策 | 总模型请求 | 实际领域工具 | 子研究 |
| --- | --- | ---: | ---: | ---: | ---: |
| 直接回答 | finish → 一次中文字段转换 | 1 | 2 | 0 | 0 |
| 工具辅助 | search_papers → finish → 中文转换 | 2 | 3 | 1 | 0 |
| 动态升级 | search_papers → run_multi_agent_research → finish → 中文转换 | 3 | 12 | 4 | 1 |

第三条实际进入原 Planner、2 个默认 Worker、Reviewer、Synthesis，然后研究 Observation 回到主 Agent 上下文。2 个模拟审核通过的 Claim/2 条引用由程序校验；模型新编写的直接回答仍标记 `not_independently_reviewed`。该演示没有触发 Recovery；其有界行为由现有回归覆盖，未伪画恢复成功。

详细 DTO、来源、调用数和主工具 trace 见 [scripted_path_examples.json](scripted_path_examples.json)。截图文件名和 [browser_check.json](browser_check.json) 明确标记模拟模型与已有真实历史的区别。

## 工程验证

本轮实际执行，原始输出保留：

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 完整 pytest | **1258 passed**，0 failed，1 既有 Starlette/httpx 弃用警告，61.55 秒 | [test_results.txt](test_results.txt) |
| 新增测试 | 38；旧基线 1220 项，没有删除旧测试 | [测试文件](../../../tests/test_react_workbench.py) |
| Ruff | exit 0 | [ruff_results.txt](ruff_results.txt) |
| 格式检查 | exit 0，265 个 Python 文件 | [formatter_results.txt](formatter_results.txt) |
| 默认服务实际启动 | 首页/API/静态资源正常；G2.1 回放；Ctrl+C exit 0 | [launch_verification.json](launch_verification.json)、[launch_results.txt](launch_results.txt) |
| 浏览器 | 三路径、中英文、刷新恢复、桌面/窄屏、首屏输入/回答、零假 Worker、终态一致、G2/G2.1 回放；无页面异常 | [browser_check.json](browser_check.json) |
| 冻结专项 | Stage E 292 个文件、47 个受保护产物通过 | [frozen_check.json](frozen_check.json) |

12 项原有未跟踪诊断测试也包含在 1258 中；本轮应提交源码的测试集合为 1246 项。原诊断文件仍由用户保留，没有擅自提交或删除。

新测试额外覆盖：工具结果反馈、命名/检索/显式范围来源、单论文、非法身份与来源、无匹配候选、正文不可用、共享额度、第二次研究拒绝、伪 Claim/Citation、失败报告保留部分事实、语言转换失败/取消、同步请求收尾、危险字段过滤和只读增强 Catalog。

Ruff 使用此前已存在的本地 0.16.5 可执行文件；项目 `.venv` 内的 Ruff 模块缺可执行文件。记录真实检查入口，不伪称仓库已配置完整的开发工具安装锁定。

## 保护与基线差异

[baseline.json](baseline.json) 保留开始时分支、HEAD、全部 tracked 摘要和原有未跟踪文件；[integrity_checks.json](integrity_checks.json) 保存逐组核验。

- Stage E **292** 个冻结文件、**412** 个历史数据/报告/测试文件、**40** 个原运行产物、**13** 个原未跟踪文件、**5** 个 Paper Index 文件、既有画像缓存均无差异。组之间可能重叠，不能相加当作唯一文件总数。
- 冻结标签与旧发布标签对象保持不变。
- 论文 fixture、Gold/AI 标签、F2/F3.1-E/G2/G2.1 原始记录未改；默认 Coverage Worker 未启用。
- **初始 `existing_index_files` 沿用了上一阶段的摘要**：现有 Evidence Index 的 `length.bin` 和 `chroma.sqlite3` 与旧摘要不同。其文件修改时间为本地 16:51，早于本轮开始 20:12；本轮实际启动前后 9 个当前文件完全一致，当前 46 条证据与绑定校验通过，向量数据/头部/邻接文件匹配旧摘要。
- 保留上述差异及旧清单，没有静默重写“初始基线”，没有覆盖文件。没有额外证明两个不同字节文件的全部历史变化原因，也不宣称其字节哈希相等；不将该旧基线差异当作新算法修改或进行索引重建。

没有新的真实 Provider 调用、Embedding 查询、画像抽取或质量评测。只读预检的“配置就绪”不等于已验证服务联网可用。

## 能力边界

1. 本地论文发现 25 篇，正文/图注证据 5 篇；无外部实时搜索，不能由元数据补正文事实。
2. 三路径用受控策略证明机制。真实模型是否稳定选择合适动作、停止、对齐问题与翻译，需另行评测。
3. 引用身份、Claim 来源及结构验证不能替代语义蕴含核验；新主 Agent 的回答不继承原单论文 Reviewer 的正确性保证。
4. 主循环仍严格单动作；模型批量动作会安全停止，不自动执行第一项。
5. 总预算有界，子研究每次最多一次；预算不足可能导致原文回退或部分结果，不保证每题在 6 步内成功。
6. 单进程内存历史；软取消等待同步依赖收尾，不能宣称即时硬进程终止。

## 阶段边界

仅本地分支 `feature/stage-g4-2-react-orchestration` 管理提交；保留原分支、标签和未跟踪诊断文件。没有远程操作、新标签或下一阶段开发。最终提交 SHA 和工作区状态另见 `delivery_record.json`，避免验收文档自引用提交哈希。
