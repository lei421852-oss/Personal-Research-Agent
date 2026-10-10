# Stage G4.3 最终发布验收

日期：2026-10-11。**结论：PASS，范围为工程发布、文档与可复核展示；不等于所有模型研究结论已经人审或真实主 Agent 的复杂协作已完成新的质量实验。**

## 1. 基线与发布对象

- 原分支：`feature/stage-g4-2-react-orchestration`。
- 原 HEAD / 生产代码版本：`84d693fa20b7ed09ce5e124fd82b16864f968db4`。
- 发布分支：`release/v1.0.0`；正式发布提交由 `git rev-parse 'v1.0.0^{commit}'` 解析，避免报告引用自身提交产生循环。
- 标签：`v1.0.0`。仅在全部检查通过后创建；保留原 `stage-e-freeze-v1`。
- 目标仓库：`git@github.com:lei421852-oss/Personal-Research-Agent.git`，本地 remote 为 `research-agent`，不使用指向旧 `Personal-agent` 的 origin。
- 按用户最后的明确指令执行正式推送，覆盖任务内较早的“仅本地”限制；不强制推送，不删除远程历史。

原工作区有 13 个未跟踪诊断文件，属于 `docs/diagnostics/`、`scripts/export_quality_failure_traces.py`、`tests/research_quality/`。保持其内容与状态，不自动加入发布。基线：[baseline.json](verification/baseline.json)。

## 2. 交付文件

| 内容 | 位置 |
|---|---|
| 正式中文项目入口 | [README](../../README.md) |
| 真实默认架构 / 研究链 | [system-overview.md](../architecture/system-overview.md) |
| 主循环、交接、共享预算与协议纠正 | [react-workbench.md](../architecture/react-workbench.md) |
| Research API / 事件 / 协作树 | [workbench.md](../architecture/workbench.md) |
| 源码环境与资源准备 | [quickstart.md](../guides/quickstart.md)，原 development 增加阶段导航 |
| 分级案例 / 演示流程 / 模拟截图 | [demo-cases.md](../demos/demo-cases.md)、[demo-guide.md](../demos/demo-guide.md)、assets |
| 简历与面试讲解 | [project-summary.md](../portfolio/project-summary.md) |
| 版本与限制 | [release-notes](v1.0.0-release-notes.md) |
| 只读发布校验 | [verify_release.py](../../scripts/verify_release.py) |

没有大规模移动目录，不新增研究能力，不修改测试预期。新增发布校验是 README 授权例外所必需的工程检查，不改变原冻结验证器。

## 3. 本轮检查

| 检查 | 本轮实测与证据 |
|---|---|
| 完整 pytest | **1265 passed，0 failed，48.89 秒**；1 项既有 Starlette/httpx 弃用警告，[日志](verification/test_results.txt) |
| 干净已提交源码离线副本 | **1251 passed、1 skipped、1 deselected，49.20 秒**，[日志](verification/clean_checkout_offline_tests.txt) |
| Ruff | PASS，[日志](verification/lint_results.txt) |
| Python 格式 | 267 files already formatted，[日志](verification/format_results.txt) |
| 冻结与受保护产物 | 经授权发布校验 PASS，详见下节，[JSON](verification/freeze_release_check.json) |
| 默认工作台实际启动 | `scripts.launch` 启动至临时本机端口；页面 / JS / CSS 返回 200，健康与 G2.1 回放可读；Ctrl+C 收尾退出 0，[JSON](verification/default_server_check.json) |
| 浏览器交互 | PASS、0 页面错误；直接 / 工具 / 协作三种 Scripted 路径、历史回放、节点点击、增量树切换、中英文、刷新与窄屏，[JSON](verification/browser_check.json) |
| CLI、链接及环境 | 真实入口帮助与发布验证，文档相对链接 / 模块来源 / 实际依赖版本分别记录于 verification |
| Git diff | 无空白错误；生产代码、语料、配置和原测试哈希未变 |
| 敏感扫描 | 拟发布文本与全部可达 Git 历史 blob 进行当前配置密钥 / 凭据模式检查；无真实密钥候选或禁提环境、模型、索引文件，[JSON](verification/sensitive_scan.json) |

本地完整回归包含 12 个未跟踪诊断测试，发布仓库不包含它们。干净副本以原 HEAD 导出，未复制 .env / 索引 / 画像缓存；发布期间生产与原测试完全相同。1 skipped 是既有依赖预检测试发现没有私有 Chroma 索引；1 deselected 是显式排除历史 25 条索引 / 24 个非空画像绑定验收。它不是完整集成测试或全新 pip 依赖环境安装证明。

本轮真实 Provider、Embedding、索引重建、训练和正式 Benchmark 均为 0。浏览器场景的 Fake 调用只验证工程，不作为模型性能。

## 4. README 唯一授权例外

原 Stage E 的 292 个冻结文件包含根 README。用户明确回复：**“允许根 README 文档例外，保留历史 SHA”**。

- 原清单和 fingerprint root 保留；291 个其他文件原样。
- 原 README 从 `stage-e-freeze-v1:README.md` 读取并核对原 SHA。
- 新 README 的 SHA 记录于[documentation_exception.json](verification/documentation_exception.json)，不扩大到算法、数据、模型或测试。
- 原冻结验证器仍返回 FAIL，仅差异 README，见[原结果](verification/original_freeze_check.json)，没有隐瞒。
- 发布专用验证分别核验这一明确授权项及全部其他文件，返回 PASS；47 个受保护产物没有变化。

新环境使用 `python -m scripts.verify_release`。某些严格旧快照 CLI 仍适用于其历史版本；当前默认工作台路径已独立检查。

## 5. 已核验的产品行为与证据强度

当前代码接入：用户问题 → MainPolicyClient / BoundedToolAgent → 可选检索 / 研究工具 → 观察反馈 → finish → 答案优先 DTO / 中文工作台。研究链仍使用默认 Worker，没有隐式启用 Coverage Worker。

真实历史来源：

- 主 Agent CLIP：1 工具、2 主决策、3 总模型调用、15.39 秒；[真实记录](../acceptance/stage_g4_2_protocol_fix/real_smoke.json)。
- 原 BLIP-2/Flamingo 问题：多动作拒绝 → 纠正 → 两次检索 → finish；6 总模型调用、28.95 秒；[真实记录](../acceptance/stage_g4_2_protocol_fix/user_question_smoke.json)。它未调用多 Agent。
- G2/G2.1：真实默认研究链的旧运行，已保存审核与不足；G2.1 4/14 已审核、10 项不足、0 完整比较，不改写为全面成功。
- Scripted 主 Agent 协作工具链与浏览器：工程测试通过，不能冒充真实模型自主协作。
- Demo C：没有可核验的真实 ReAct 主 Agent 协作产物，明确待用户手动验收，不生成假的日志或声称人工已核实。

完整实验边界、独立人工 Gold 为 0、样本分母及成本混杂见[实验汇总](../reports/experiment-summary.md)。历史事实记录不修改。

## 6. 未解决问题与严重程度

| 问题 | 程度 / 处理 |
|---|---|
| 尚无已保存真实主 Agent 自主协作示例 | 展示证据缺口，明确标注待手动验收；不影响已验证接口与工程发布，不宣称该真实实验通过 |
| 干净 checkout 没有私有向量索引及画像 | 资源依赖；指南提供显式准备路径，离线测试与完整集成分开 |
| 历史索引验收包含 24 个画像数量 | 历史环境绑定，不能要求新 LLM 抽取复制该数；不更改测试预期 |
| 模型路由、停止、语义遗漏或幻觉仍可能发生 | 已知模型限制；程序边界不等于完整语义验证 |
| VLM 配置检查与文本资源装配仍耦合 | 既有配置边界，文档准确说明；没有趁发布重构 |
| 包元数据 0.1.0 / 源码 checkout | 冻结与分发边界；v1.0.0 是工程 Git 版本，不冒充完善 wheel 分发 |
| Starlette/httpx 弃用警告 | 非阻塞依赖提醒，未升级或新增工具修复 |
| 进程内历史与软取消 | 既有工程取舍，明确说明 |

没有发现未经授权的冻结修改、秘密泄漏、生产接口破坏或其他发布阻断项。PASS 对应工程 / 文档范围，保留上述限制。

## 7. Git 交付核验

发布以文档、演示材料、最终验收分别提交；完整提交与推送目标在最终用户交付记录中列出。最终标签解析的提交须与 GitHub `main` 相同；保留既有用户未跟踪文件，不执行 force push。推送后再通过远程 ref 核验，而不只依赖本地成功消息。


远程 main 原有五个文档提交导致第一次非强制原子推送被拒绝，0 个 ref 更新。随后只获取并正常合并旧文档，保留全部原产物，唯一导航冲突融合双方链接。合并后再次完整测试1265通过；没有 force push 或生产代码变更。证据见 [remote_integration.json](verification/remote_integration.json)。尚未发布的本地 v1.0.0 标签随后指向最终复核提交。
