# 正式展示案例与证据来源

日期：2026-10-11。本阶段没有新增真实 Provider 实验，也没有挑选重跑后的最佳结果。历史 V0.1 的 A/B/C 与本文命名不同，原文件保留。

## Demo A：CLIP 简单问题

展示输入：`CLIP 使用什么训练目标对齐图像和文本？`

已保存的真实运行使用英文原问题：`What training objective does CLIP use to align images and text?`。中文措辞作为演示输入，不声称该中文原文已运行。

- 真实主 Agent 自主选择 CLIP scope，执行 `search_text → finish`。
- 1 次实际工具，2 次主决策，3 次总模型调用（含中文展示）；耗时 15,394.21 ms。
- 3 条引用来自 CLIP 第 2 / 4 页。回答包含对比目标与对称交叉熵。
- 没有调用多 Agent，直接回答没有 Reviewer 独立语义审核。
- 来源：[真实脱敏记录](../acceptance/stage_g4_2_protocol_fix/real_smoke.json)、[同阶段验收](../acceptance/stage_g4_2_protocol_fix/acceptance_report.md)。记录创建于 2026-10-10 协议修复、2026-10-11 完成整理；不是 G4.3 新实验。

## Demo B：两篇论文的检索比较

真实原问题：

> 比较 BLIP-2 与 Flamingo 的视觉语言连接机制，说明 Q-Former、Perceiver Resampler 与门控交叉注意力如何将视觉特征传递给语言模型，并引用当前论文库中的证据。

- 首轮 2 个动作被整批拒绝，0 个工具执行；反馈后模型恢复单动作。
- `search_text(BLIP-2)` 返回 5 条，`search_text(Flamingo)` 返回 6 条，再 finish。
- 4 次主决策（含 1 次协议纠正）、2 次工具、6 次全部模型调用、28,950.22 ms。
- 回答含接口差异、真实引用与限制；工作台应显示线性路径。
- 没有调用研究工具；不能把该记录称为 Planner / Worker / Reviewer / Synthesis 已协作或已审核比较。
- 来源：[原问题真实记录](../acceptance/stage_g4_2_protocol_fix/user_question_smoke.json)。

## Demo C：复杂逐论文研究

可复制问题：

```text
请使用多 Agent 协作研究，比较 BLIP-2 与 Flamingo 的视觉语言连接机制。
先规划需求，再由各论文 Worker 分别收集三个维度的证据：
1. 视觉特征如何被连接模块提取、压缩或转换；
2. 转换后的视觉信息如何进入语言模型；
3. 连接模块、视觉编码器与语言模型的冻结和训练范围。
请审核提交论断，再综合生成中文比较报告。
先直接回答机制差异，再给出逐维度比较、页码引用和证据缺口。
```

**真实主 Agent 自主协作运行：待用户手动验收并保存可核验产物。** 当前仓库没有对应真实主 Agent 的交接、完整协作事件和最终回答记录，不能仅凭用户提出过该问题或界面截图标为真实通过。

可独立复核的工程证据是 Scripted 主策略调用 `run_multi_agent_research`，运行既有真实领域组件与 Fake Provider，再把报告观察交回主循环：见[原主编排验收](../acceptance/stage_g4_2_react/acceptance_report.md)及[协作树浏览器验收](../acceptance/workflow_tree/browser_check.json)。它验证连线与约束，不验证真实模型自主研究质量。

已有真实 G2/G2.1 默认研究链可以通过顶部历史回放展示 Worker、Reviewer、Synthesis、缺失和失败。它们发生在主 ReAct 工具编排接入前，不能证明本次主 Agent 自主选择研究工具。G2.1 虽合法结束，4/14 已审核、10 项不足、0 完整比较，必须原样展示。

- [G2 原始案例](blip2-vs-flamingo/README.md)
- [G2.1 真实结果](../acceptance/stage_g2_1/results/execution-summary.json)
- [G2/G2.1 指标口径](../reports/experiment-summary.md)

## 验证强度

真实 Provider 调用、Scripted 工程测试、历史回放、用户口头反馈和独立人工事实核验分别记录。图片显示不证明 VLM 调用；完成状态不证明比较正确；Reviewer passed 不等于需求完整或独立人审。
