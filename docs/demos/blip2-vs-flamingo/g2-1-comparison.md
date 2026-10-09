# G2 → G2.1：Worker 工具协议验证

**G2.1协议工程验收PASS；完整研究主题仍为CONDITIONAL。** 本页不覆盖G2原始演示文件。G2.1只正式运行一次，之后停止。

## 修复边界

冻结默认`PaperResearchWorker`、Controller、Tool input_model和Provider未改写。新`WorkerProtocolAdapter`只增强模型请求副本的单动作说明、paper-local Schema与字段反馈；响应原样返回。Reviewer、Recovery、Synthesis使用原类和规则，未启用Coverage Worker。

原共用search_text描述包含“跨论文比较要检索所有论文”，但单论文Worker只能查当前论文。模型所见副本现在与已有Controller一致：paper_ids是本论文单元素列表，paper_id不能作为search参数；批量resolution允许放在一个resolve_requirements内部，不能返回多个function calls。

多动作/参数错误示例由实际Schema和当前任务身份生成，错误工具从既有Trace绑定。Controller不挑动作、不改参数、不扩大scope，不额外发模型请求，也不增加纠正预算。

## 实测对比

| 指标 | G2 | G2.1 |
| --- | ---: | ---: |
| Worker决策 /模型尝试 | 24 | 12 |
| Worker multiple_actions | 4 | 0 |
| Worker invalid_arguments | 8 | 0 |
| 已使用Worker协议纠正机会 | 3 | 0 |
| completed Worker | 1/2 | 2/2 |
| execution_failed需求 | 6 | 0 |
| 合法resolved需求（含不足） | 6/12 | 14/14 |
| 已审核需求 | 5/12 | 4/14 |
| insufficient_evidence需求 | 1 | 10 |
| 跨论文比较 | 0 | 0 |
| 全链LLM调用（含Planner） | 31 | 19 |
| 实际工具调用 | 9 | 8 |
| 总耗时（含预检） | 41.96s | 52.69s |
| Synthesis协议纠正 | 0 | 1 |

Worker多动作率4/24→0/12，参数错误率8/24→0/12。LLM调用下降38.71%、工具下降11.11%，墙钟耗时上升25.58%；不能声称成本所有方面都降低。VLM、Provider transient retry均为0。

本次没有触发新增Worker错误反馈，真实结果支持预防性协议约束有效执行；错误后恢复能力由17项Fake测试证明，不将其描述成这次真实模型做了参数自我纠错。

## 研究主题为何仍未完成

相同问题重新调用Planner后，维度6→7、需求12→14。新维度有BLIP-2 /Flamingo专属机制，却仍按Paper × Dimension展开：例如Flamingo收到Q-Former问题，BLIP-2收到Perceiver Resampler问题。未修改Planner或计划来美化结果。

BLIP-2通过3条Claim；Flamingo通过1条Perceiver Resampler Claim，其余单元为不足。没有同一维度的双论文VerifiedClaim，不能构造结构合法的比较，所以Synthesis比较数0。Flamingo一次检索后便批量提交其余不足；这些缺口是否合理、是否过早停止需要独立语义复核，本轮不把声明不足自动计为质量成功。

两次计划不相同，不能按5/12→4/14判定同题语义改善或退化，不能把单次结果当成稳定泛化结论。真实报告见[本次研究报告](../../acceptance/stage_g2_1/results/research-report.md)，引用见[证据映射](../../acceptance/stage_g2_1/results/evidence-map.md)。

综合第一次输出有invalid_evidence_reference，被原校验器拒绝；一次有界纠正后通过。该类型不等于已确认虚构ID或“幻觉1次”，最终报告没有结构性引用违规。Synthesis规则未修改。

## 运行入口与复核

源码仓库根目录，先预检：

```bash
python -m scripts.run_worker_protocol_demo --output-dir artifacts/my-g2-1-validation
```

预检确认G2原问题、论文、模型名/host、配置、预算、420秒等待限制和索引指纹一致后执行：

```bash
python -m scripts.run_worker_protocol_demo --output-dir artifacts/my-g2-1-validation --execute
```

历史G2入口仍冻结，不会自动使用修复。必须显式使用本新版入口；这不是默认启用Coverage或改写历史基线。已有正式执行目录禁止覆盖。此处命令供后续独立复现，本轮没有第二次真实验证。

[诊断](../../acceptance/stage_g2_1/diagnosis.md)、[验收报告](../../acceptance/stage_g2_1/acceptance_report.md)、[机器可读对比](../../acceptance/stage_g2_1/comparison_summary.json)和[原始字段副本](../../acceptance/stage_g2_1/results/execution-record.json)分别记录事实、假设、成本和来源。完整非敏感原始执行保留在ignored artifacts和用户交付包。模型审核不代替人工事实审核。
