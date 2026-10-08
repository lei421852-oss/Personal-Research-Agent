# Stage G2：真实论文研究案例验收

日期：2026-10-09。**工程验收PASS；G2整体CONDITIONAL。** 唯一一次真实多论文执行完成检索、模型审核和正常综合生成，但Flamingo Worker失败，没有形成有效跨论文比较。不得将该案例称为成功完成BLIP-2 /Flamingo研究主题。

## 1. 实际执行与配置

原分支`docs/stage-g1-architecture`，起点HEAD `88e9fb0e40a665a8d80dc0ebd73504ad1c088e88`。新本地分支`docs/stage-g2-research-demo`。正式执行时入口代码在提交`fb7a0e4`中。

执行命令（源码仓库根目录）：

```bash
python -m scripts.run_research_demo --help
python -m scripts.run_research_demo --output-dir artifacts/stage-g2-blip2-flamingo
python -m scripts.run_research_demo --output-dir artifacts/stage-g2-blip2-flamingo --execute
```

实际使用仓库`.venv/bin/python`，一次独立环境预检、一次正式执行，没有真实Smoke后再选择正式成功的重跑。配置使用原`configs/cross_paper_eval.json`：Qwen/Qwen3-Embedding-0.6B、cpu、cosine、既有research-stage-e-text collection，46条文本。语料snapshot：`18fce87eb4050186aefc545a97b55d8b397108953647c5985c685102b2ea2390`。

LLM /VLM配置均为当前deepseek-flash /api.deepseek.com；本次VLM实际调用0，不由配置字段推断视觉能力。模型参数由原Provider Adapter管理：Tool Calling、parallel_tool_calls=False、DeepSeek thinking disabled、temperature未显式发送、SDK max_retries=0，外层仍按原transient retry策略处理。

初始Worker预算模型20 /工具8、协议纠正2、单次依赖等待60秒；LLM并发2 /Worker并发3 /检索并发1。Reviewer每需求模型3，Synthesis模型5 /纠正2；原Recovery最多一轮，研究总预算模型200 /工具64。封装为内层研究等待设置420秒，清理遵守原drain语义，实际没有超时。

入口只打开已有索引（create=False），不调用旧real_retriever的补建分支；模型仅使用缓存。索引前后逻辑指纹相同：`266d1057540f44d6dc84826053d5a9b62e6b705fdc7dbbaad749fc1f4a43aeab`。

## 2. 实际研究结果

Planner真实调用1次，生成6个维度、12个Paper × Dimension需求，保存在[紧凑执行记录](../../demos/blip2-vs-flamingo/execution-record.json)。全部维度和问题由模型提出，未按预期答案手写或删减。

| 项目 | 结果 |
| --- | --- |
| BLIP-2 Worker | completed：5 supported，1 insufficient_evidence |
| Flamingo Worker | failed：6 execution_failed，停止原因protocol_correction_exhausted |
| Reviewer | 5 passed，0 semantic rejected，7 not_reviewable |
| Recovery | 检查12个需求；eligible=0，实际恢复Worker=0、轮数0 |
| Synthesis | 1次真实模型调用，报告结构completed，未降级 |
| 证据覆盖 | 5/12 =41.67% |
| ResolutionRate | 6/12 =50%（5 supported +1合法不足） |
| 跨论文比较 | 0；比较引用有效率无分母，保持null |

实际模型报告仅留下BLIP-2的接口架构、视觉特征传递、查询提取、训练阶段和交互粒度论断。它描述Q-Former查询表示经投影后作为LLM输入的视觉提示；这些是**模型生成且模型Reviewer通过的结论**，不是本轮独立人工核验的事实。Flamingo没有VerifiedClaim，不补写其Perceiver Resampler、Gated Cross-Attention或两者差异。

原报告：[research-report.md](../../demos/blip2-vs-flamingo/research-report.md)。BLIP-2 cross_attention_gating不足与Flamingo执行失败严格区分；后者不能计为正确拒答。报告completed /ReviewPassRate=100%均不能代替任务完成率。

## 3. 已证实失败与保留风险

Flamingo在Steps 1/5/10分别返回3/4/2个actions。Controller拒绝全部，多动作从未被执行；前两次反馈后模型继续决策，第三次拒绝时纠正预算已用尽。它在Steps 8/9成功检索和读页，观察到账本8条证据，但未提交合法研究需求。

中间还出现5次search参数schema失败（paper_id额外字段）；BLIP-2出现3次同类参数失败、1次冗余读页及1次多动作拒绝，随后恢复到5个supported。原Trace只公开契约允许的参数，错误字段由failure.validation_errors提供。详见[逐步失败分析](../../demos/blip2-vs-flamingo/failure-analysis.md)。

该失败首先出现在Worker Policy单动作协议，未发现索引缺失、Provider连接故障、审核绕过或引用伪造。连续协议违规的更深原因及可能优化效果仍未验证。Recovery原本只恢复合法语义审核rejected，因此正确跳过execution_failed。没有扩大本阶段去修Worker、Reviewer或Prompt。

## 4. 成本与计数

| 边界 | 实际值 |
| --- | --- |
| LLM实际尝试 | 31 = Planner1 +Worker24 +Reviewer5 +Synthesis1 |
| 工具实际调用 | 9 = search_text6 +read_page3 |
| VLM | 0 |
| Provider transient retry | 0 |
| 多动作拒绝 /允许纠正 | 4 /3（BLIP-2一次、Flamingo两次） |
| 封装总耗时 | 41.96秒，含正式预检/本地模型准备 |
| Planner与研究耗时 | 35.66秒 |
| 内层默认研究链 | 31.30秒 |

正式命令Embedding调用为6次检索 +1次可用性检查；独立预检额外1次，共8次该边界调用。不把协议反馈、未执行动作或Requirement提交算作真实工具调用。未获取SDK token usage或金额，不报告虚构货币成本。

内层run_case仍记录Planner调用0，它只消费外层新生成的plan；总体Planner1有真实provider事件。内部继承的旧“冻结计划重放”描述不作为本次事实声明。Recovery metrics.additional_model_calls=1包含Synthesis调用，不意味着运行了恢复Worker。

## 5. 引用、展示与原始记录核验

[verification.json](verification.json)证明：

- 展示报告逐字等于原ResearchReport的既有renderer输出；没有人工修订Claim或缺口。
- 5个已审核Claim，共13处引用、5个去重TextEvidence，均来自BLIP-2 PDF Pages 2/3/4，身份、论文、页码、文本与当前Corpus一致。
- 每个Claim匹配最终merged Reviewer passed记录；SynthesisInput和报告结构经原audit_report再次校验。
- 提交的execution-record只是选择原始字段，原始完整执行SHA256为`a5e5b2603063a1e5cf7c4a60313c57c3c70c9980ec773c4558271428c84cad5b`。
- 凭据扫描0命中，隐藏推理字段0；只读核验没有新增Provider调用。

证据地图区分模型论断、程序身份校验与尚未完成人工语义核验。完整执行247237字节和逐次检索留在ignored artifacts目录，完整交付包包含原始记录；Git只提交106947字节的非敏感字段副本、报告、摘要与指纹。

## 6. 工程检查与文件保护

正式执行前已实际运行：`python -m pytest -q` **1054 passed、0 failed、1既有warning，15.16秒**；相较G1新增11项封装契约测试。Ruff全范围通过，227个Python文件格式通过。详见[checks_before_execution.json](checks_before_execution.json)。

运行后只读一致性核验通过：Stage E冻结292文件、F2等47个登记保护产物、F3.1/F3.1-E额外60个产物，以及起点480个tracked文件与13个原未跟踪文件均保持哈希。没有启用Coverage、重建生产索引、修改参考标签、执行90题或远程操作。

CLI和输出目录测试、帮助检查、报告/证据对应测试通过。文档链接与命令参数检查见[documentation_checks.json](documentation_checks.json)。正式入口进程退出0表示合法报告已写出且索引未变，不能据此宣布完整研究主题成功。

## 7. 交付、Git与验收决定

[案例目录](../../demos/blip2-vs-flamingo/README.md)包含README、question、实际research-report、evidence-map、execution-summary、limitations、failure-analysis及紧凑执行记录。

新增入口和测试提交：`fb7a0e4`（feat: add reproducible default research demo entry）。演示和验收材料另行本地提交，最终记录见[git_delivery.json](git_delivery.json)。原13个未跟踪文件原样保留，没有远程push/pull。

G2工程条件和失败交付完成；**展示目标未完整达成，保持CONDITIONAL，不作为成功的两论文比较Demo对外宣传**。可用于展示有界协议失败、可追溯检索、审核准入、缺失保留和安全综合。若需要成功比较案例，后续须另行授权处理协议/策略问题；本轮不调优、不重跑、不启动下一阶段。
