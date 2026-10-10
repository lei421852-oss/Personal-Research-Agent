> 当前默认入口：ReAct 主 Agent。只输入问题，多论文研究是可选工具；原 Prepared-only 选择/手选界面为历史说明。[自主编排架构](architecture/react-workbench.md) / [使用指南](guides/react-workbench.md)。

# 项目文档导航

**v1.0.0 当前入口：** [正式 README](../README.md) · [Quickstart](guides/quickstart.md) · [演示与验证边界](demos/demo-cases.md) · [简历与面试](portfolio/project-summary.md) · [最终验收](releases/final-acceptance-report.md)。

**历史 G4.2 发布资料：** [旧项目主页](project/README.md) · [旧工作台展示](project/showcase.md) · [技术亮点](project/highlights.md) · [旧求职材料](project/resume-and-interview.md)。这些文件与原验收产物保留，旧“根README仍冻结不更新”说明适用于当时，当前仅根README文档例外已经用户授权。

**2026-10-10 中文动态工作台阶段：** [旧使用说明](guides/ui-quickstart.md) · [阶段架构](architecture/chinese-dynamic-workbench.md) · [验收记录](acceptance/stage_g4_2_chinese_dynamic_ui/acceptance_report.md)。当前默认入口以顶部 ReAct 与 v1.0.0 文档为准。

本导航于 **2026-10-09 / Stage G1** 根据仓库源码整理。阅读顺序：

1. [系统架构与能力边界](architecture/system-overview.md)：先区分单 Agent 演示和多论文研究链路。
2. [工程现状清单](architecture/engineering-inventory.md)：模块、模型、入口、数据与依赖的源码位置。
3. [开发与使用指南](guides/development.md)：环境、语料校验、命令及常见问题。
4. [G1 文档与目录审计](architecture/documentation-audit.md)：历史说明的适用范围与保留原因。
5. [G1 验收报告](acceptance/stage_g1/acceptance_report.md)：本轮检查、完整回归与保护文件核验结果。

## 基线、历史阶段与实验

| 材料 | 如何理解 |
| --- | --- |
| [仓库 README](../README.md) | v1.0.0 正式主页；经用户授权更新唯一根README文档项，原SHA由历史标签保留。算法与数据冻结不变。 |
| [Stage E 冻结清单](acceptance/stage_e_freeze/freeze_manifest.json) | 292 个文件的原始 SHA256；冻结的是这些文件，不是禁止增加独立文档或实验组件。 |
| [Planner](research-planner.md)、[异步 Worker](async-research.md) | 对应实现阶段的职责与验收；不能用早期“不含 Reviewer/Synthesis”判断当前仓库没有后续模块。 |
| [Reviewer](evidence-review.md)、[Recovery](research-recovery.md)、[Synthesis](research-synthesis.md) | 当前研究链路的模块说明，配合 G1 总览阅读。 |
| [论文发现](paper-discovery.md)、[查询扩展](query-expansion.md)、[离线画像](paper-profiles.md) | 元数据发现不等于正文证据可用；这些能力没有自动构成开放式全流程研究入口。 |
| [Stage E 评测](stage-e-evaluation.md) | 工程评测和引用结构检查，不代表独立人工语义质量 Benchmark。 |
| [质量评测结构](research-quality-benchmark.md)、[执行说明](research-quality-execution.md) | 候选集、AI 审核参考集与人工 Gold 必须分开。 |
| [F2 原始结果](acceptance/quality_end_to_end/results/run_manifest.json) | 保存的 90 题执行基线；G1 不重新评测、不改标签。 |
| [Coverage Worker](worker-coverage.md)、[F3.1-E 报告](acceptance/stage_f3_1_e/acceptance_report.md) | 显式可选实验；七题是已知失败诊断集，不能证明未知数据的泛化，也没有默认替换旧 Worker。 |
| [历史 Demo](demos/README.md) | 每条 Demo 的 Real / Scripted / Documented 状态及检索模式必须保留；不能把受控检索或历史 VLM Smoke 当作当前研究视觉质量结论。 |

以上为历史 G1 阶段范围；当前默认入口与可选研究工具请以本页顶部 ReAct 说明为准。
