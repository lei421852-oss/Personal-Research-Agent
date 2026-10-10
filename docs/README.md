# 项目文档导航

**当前发布入口：[项目主页](project/README.md) · [工作台展示](project/showcase.md) · [技术亮点](project/highlights.md) · [简历与面试](project/resume-and-interview.md)。**

当前使用 `python -m scripts.launch` 启动独立多论文工作台；见 [UI快速开始](guides/ui-quickstart.md)。下方G1导航和阶段说明保留历史语境；根README仍是Stage E冻结快照。

本导航于 **2026-10-09 / Stage G1** 根据仓库源码整理。阅读顺序：

1. [系统架构与能力边界](architecture/system-overview.md)：先区分单 Agent 演示和多论文研究链路。
2. [工程现状清单](architecture/engineering-inventory.md)：模块、模型、入口、数据与依赖的源码位置。
3. [开发与使用指南](guides/development.md)：环境、语料校验、命令及常见问题。
4. [G1 文档与目录审计](architecture/documentation-audit.md)：历史说明的适用范围与保留原因。
5. [G1 验收报告](acceptance/stage_g1/acceptance_report.md)：本轮检查、完整回归与保护文件核验结果。

## 基线、历史阶段与实验

| 材料 | 如何理解 |
| --- | --- |
| [仓库 README](../README.md) | Stage E 冻结材料，保留当时的数字和阶段说明；其中“当前测试数”和部分“尚未实现”应结合阶段日期阅读。G1 不改写冻结文件。 |
| [Stage E 冻结清单](acceptance/stage_e_freeze/freeze_manifest.json) | 292 个文件的原始 SHA256；冻结的是这些文件，不是禁止增加独立文档或实验组件。 |
| [Planner](research-planner.md)、[异步 Worker](async-research.md) | 对应实现阶段的职责与验收；不能用早期“不含 Reviewer/Synthesis”判断当前仓库没有后续模块。 |
| [Reviewer](evidence-review.md)、[Recovery](research-recovery.md)、[Synthesis](research-synthesis.md) | 当前研究链路的模块说明，配合 G1 总览阅读。 |
| [论文发现](paper-discovery.md)、[查询扩展](query-expansion.md)、[离线画像](paper-profiles.md) | 元数据发现不等于正文证据可用；这些能力没有自动构成开放式全流程研究入口。 |
| [Stage E 评测](stage-e-evaluation.md) | 工程评测和引用结构检查，不代表独立人工语义质量 Benchmark。 |
| [质量评测结构](research-quality-benchmark.md)、[执行说明](research-quality-execution.md) | 候选集、AI 审核参考集与人工 Gold 必须分开。 |
| [F2 原始结果](acceptance/quality_end_to_end/results/run_manifest.json) | 保存的 90 题执行基线；G1 不重新评测、不改标签。 |
| [Coverage Worker](worker-coverage.md)、[F3.1-E 报告](acceptance/stage_f3_1_e/acceptance_report.md) | 显式可选实验；七题是已知失败诊断集，不能证明未知数据的泛化，也没有默认替换旧 Worker。 |
| [历史 Demo](demos/README.md) | 每条 Demo 的 Real / Scripted / Documented 状态及检索模式必须保留；不能把受控检索或历史 VLM Smoke 当作当前研究视觉质量结论。 |

本轮不新增研究功能，不移动生产模块，不调用真实 Provider，不重建索引。API/UI 仍然是已有单 Agent 入口；多论文研究报告通过独立脚本或库组合运行。
