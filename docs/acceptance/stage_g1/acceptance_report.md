# Stage G1 中文验收报告

日期：2026-10-09。结论：**PASS — 工程整理与文档验收通过**。本轮不代表新的研究质量评测或视觉能力验收。

## 完成内容

1. [系统架构](../../architecture/system-overview.md)：两条真实入口、Planner / Worker / Reviewer / Recovery / Synthesis 职责、两张 Mermaid、证据准入、失败分支、预算与取消边界。
2. [工程清单](../../architecture/engineering-inventory.md)：实际模块、领域模型、工具与索引、配置、脚本、测试、依赖、实验功能。
3. [开发指南](../../guides/development.md)：依赖组、环境变量、只读校验、具有副作用的准备与索引命令、API 启动、评分路径、常见错误。
4. [文档导航](../../README.md) 与 [审计记录](../../architecture/documentation-audit.md)：说明冻结 README 和历史阶段说明的适用范围，未重写历史产物。
5. 本目录保存基线、CLI 帮助、实际测试/静态日志、语料校验、研究预检、链接/参数检查与最终完整性记录。

所有变更均为新增文档和本轮验收记录。没有生产逻辑变更，没有移动代码，没有新增工具，没有调整检索/Prompt，没有清理既有诊断，没有默认启用 Coverage Worker。

## 架构与能力结论

- API/UI 是 BoundedToolAgent 单问题演示入口；多论文研究通过脚本或库组合进入，未接入当前 UI。
- Paper Catalog 为25篇元数据；研究 Prepared Corpus 为5篇、46条文本、5条 Figure；API 默认使用原3篇、25条文本、3条 Figure。
- 当前研究主要基于 Prepared PDF 文本和图注。代码有可选 image_url 发送与 FigureAnalyzer 机制；Reviewer 不读取像素，既有七题对照 VLM=0。本轮不把其描述成已验证的像素级视觉推理。
- Synthesis 只接受 supported、结构验证通过且精确绑定审核 passed 的 Claim；缺失状态保留。引用身份和结构校验不等于语义正确性证明。
- Coverage Worker 继续作为显式实验组件；七题已知失败样本的质量与成本权衡保持原报告，不外推到未知数据。

## 本轮实际验证

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 完整 pytest | **1043 passed，0 failed，1 warning，14.43 s，退出码0** | [test_results.txt](test_results.txt) |
| Ruff | All checks passed，退出码0 | [lint_results.txt](lint_results.txt) |
| formatter | 225 files already formatted，退出码0 | [lint_results.txt](lint_results.txt) |
| CLI 真实性 | 18个已读源码入口 --help 均退出0 | [cli_help.json](cli_help.json) |
| Corpus 校验 | status=valid；未初始化模型；五篇 snapshot 保持一致 | [corpus_validation.json](corpus_validation.json) |
| 研究预检 | two-paper-bridge 为 not_executed，real_provider_calls=0、models_initialized=0 | [research_preflight.json](research_preflight.json) |
| 文档与命令 | 新文档本地链接、模块、参数与相关源码契约核验通过 | [documentation_checks.json](documentation_checks.json) |
| 文件保护 | Stage E、F2、F3.1/F3.1-E 以及本轮开始前所有文件哈希匹配 | [integrity_check.json](integrity_check.json) |

执行详情和退出码见 [check_commands.json](check_commands.json)。沿用既有 Ruff 范围，没有新增 linter 或 type-checker。唯一警告是现有 Starlette TestClient 对 httpx 的弃用提示，不阻塞测试。本轮没有针对文档变化新增重复性测试。

安装、fixture preparation、索引构建、启动服务及真实评测命令仅通过源码、参数和帮助检查，不声称已实际执行。完整 pytest 使用测试依赖，不运行新的真实模型实验；Provider 调用0，索引重建0。

## 基线与保护

- 原分支：`feature/stage-f3-1-worker-coverage`；原 HEAD：`3be3a68a87c72a22456908c9b655afcfc8c422e5`。
- 本地工作分支：`docs/stage-g1-architecture`。
- Stage E tag 对应 commit：`5700c6e31d0a46fefa44f890fb7fbb1b60634124`；annotated tag 对象另外记录，不能将 tag 对象 SHA 当 commit SHA。
- Stage E：292个冻结文件，原聚合指纹 `44c7afd36c793eff86faed077b7c917a5c6a200e3d652bfbf5b19e83cff0362e`。
- F2 等基线登记保护产物：47个；F3.1/F3.1-E 本轮额外登记保护产物：60个。
- 原 tracked 文件：462个全部保持；原未跟踪文件：13个保持内容与未跟踪状态。保护集合可能相互重叠，数量不得相加作为独立文件总数。

[baseline.json](baseline.json) 记录原状态、Python / 依赖和逐文件 SHA256；[integrity_check.json](integrity_check.json) 是本轮结束时的复核。没有读取或写入真实 API Key，Git 跟踪的环境文件只有 `.env.example`。

## 未解决事项及严重程度

| 事项 | 程度 / 处理 |
| --- | --- |
| 根 README 的“当前测试数”和部分历史阶段范围容易过期 | 非阻塞文档风险；它是冻结文件，本轮以新导航和总览解释，不改原内容。 |
| 没有依赖锁文件，包元数据0.1.0，scripts/eval 不纳入 wheel | 非阻塞工程限制；本轮记录实际安装与源码运行方式，不改打包。 |
| Starlette / httpx 现有弃用提示 | 非阻塞兼容性提示；未扩大任务升级依赖。 |
| 研究视觉质量、AI参考非人工Gold、Coverage权衡、无研究API/UI | 原有能力边界，不是本轮新缺陷；不运行新实验或新增功能。 |

没有发现阻塞 G1 的问题。建议接受本轮文档和工程整理；这不解除 Stage E 冻结约束，也不宣布 Coverage Worker 达到默认上线条件。

## Git 与阶段结束

按工程审计、架构文档、开发指南及验收记录拆分本地提交；提交清单见 [git_delivery.json](git_delivery.json)。仅提交本轮新增文件，原13个未跟踪文件未被纳入。没有远程 push/pull，没有创建新版本 tag。

完成 G1 后停止，未自动进入 G2。
