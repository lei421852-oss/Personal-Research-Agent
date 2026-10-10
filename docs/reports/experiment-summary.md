# Stage E—G2.1 实验汇总与口径审计

核验日期：**2026-10-10**。所有数字来自既有原始JSON/JSONL/CSV，G3没有调用Provider或重跑研究。审计结果含文件SHA256与字段位置：[E/F2审计](../acceptance/stage_g3/stage_e_f2_audit.json)、[F3系列审计](../acceptance/stage_g3/stage_f3_audit.json)、[G2轨迹重算](../acceptance/stage_g3/g2_audit.json)。架构与案例见[技术报告](technical-report.md)。

## 1. 实验身份和可比较范围

| 阶段 | 目标 /样本 | 执行配置与入口 | 原始证据及限制 |
| --- | --- | --- | --- |
| E | Prepared Corpus来源、15条核心检索、双/三论文工程研究；静态登记Case22 | Qwen/Qwen3-Embedding-0.6B、cpu、cosine；检索评测Top-K10；研究重放已保存计划；deepseek-flash、原Worker | [检索原件](../acceptance/stage_e_freeze/evidence/2026-10-08-多模态论文研究智能体-StageE真实检索回归.json)、[双论文](../acceptance/stage_e_freeze/evidence/2026-10-08-多模态论文研究智能体-StageE双论文真实评测.json)、[三论文](../acceptance/stage_e_freeze/evidence/2026-10-08-多模态论文研究智能体-StageE三论文真实评测.json)。不是22次真实研究，受控Smoke另列。 |
| F2 | 90混合任务，dev18 /test72，评估既有链与模块 | 5篇46文本5Figure；同名Qwen/cpu索引；deepseek-flash；按题目构造固定论文/维度计划，Planner未作为真实被测对象 | [原执行90行](../acceptance/quality_end_to_end/results/executions.jsonl)、[评分](../acceptance/quality_end_to_end/results/quality-score-all.json)、[manifest](../acceptance/quality_end_to_end/results/run_manifest.json)。AI参考90，人工Gold0。 |
| F3诊断 | F2七条失败比较的首次错误定位 | 只读现存Worker /Reviewer /Synthesis，0新Provider /0新检索 | [F2原执行](../acceptance/quality_end_to_end/results/executions.jsonl)为可克隆权威来源；本地七题诊断原件属于原未跟踪文件，G3不将其静默提交。不是另一次真实实验。 |
| F3.1 | 新覆盖提交、范围与需求完整门槛的工程验收 | 可选CoverageAwarePaperWorker；4条受控合成轨迹；0真实Provider | [验收](../acceptance/stage_f3_1/acceptance_report.md)、[覆盖说明](../worker-coverage.md)。历史955项通过 /新增81项，不是语义性能。 |
| F3.1-E | 7条已知失败的真实配对诊断 | 同原F2问题、论文、维度、计划与预算；Qwen/cpu、既有46文本索引；显式Coverage Worker，原审核/恢复/综合 | [原对照](../acceptance/stage_f3_1_e/results/comparison_summary.json)、[逐题CSV](../acceptance/stage_f3_1_e/results/comparison_results.csv)、[验收](../acceptance/stage_f3_1_e/acceptance_report.md)。非盲AI辅助，人工0，不能泛化。 |
| G2 | BLIP-2 /Flamingo连接机制，一次真实演示 | 原默认Worker、同研究索引和deepseek-flash；真实Planner生成6维度12需求 | [紧凑原记录](../demos/blip2-vs-flamingo/execution-record.json)、[摘要](../demos/blip2-vs-flamingo/execution-summary.json)。工程通过，但主题CONDITIONAL，比较0。 |
| G2.1 | 同主题一次协议稳定性验证 | 原Worker加请求协议适配；模型/配置/索引同G2，重新Planner生成7维度14需求；不启用Coverage | [原验证](../acceptance/stage_g2_1/results/execution-record.json)、[前后统计](../acceptance/stage_g2_1/comparison_summary.json)。计划变化，协议工程PASS不等于研究质量PASS。 |

共同研究配置见[配置文件](../../configs/cross_paper_eval.json)：初始Worker模型20 /工具8，多动作纠正2；Reviewer每需求3；资源槽Worker3 /LLM2 /VLM1 /检索1，操作等待60秒；Synthesis模型5。某次搜索的top_k仍以实际工具参数为准，不能据配置10断言所有研究工具调用都检索10条。具体运行差异保留在各manifest。

## 2. Stage E：覆盖和真实工程链

| 检索数据范围 | 样本分母 | Macro Recall@5 | Macro Recall@10 |
| --- | ---: | ---: | ---: |
| 原3篇Corpus | 15 | 24.00% | 33.33% |
| 新5篇Corpus | 15 | 74.00% | 96.67% |

新增BLIP-2 /LLaVA后，原来缺少正文的需求可检索。该对照改变了语料可用性，不是相同Corpus上的算法优劣；相关标注为source-checked候选，不是独立人工审核的穷尽相关集。来源指针`/retrieval_metrics/before_stage_d_corpus`与`after_stage_e_corpus`，逐行重算一致。

| 真实案例 | 论文 /需求 | 已审核覆盖 | 比较 | 模型/工具 | 案例耗时 |
| --- | --- | ---: | ---: | --- | ---: |
| two-paper-bridge | **BLIP-2 /LLaVA**，1维度2需求 | 2/2 | 1 | 9 /2 | 15.18s |
| 三论文研究 | BLIP-2 /Flamingo /LLaVA，5维度15需求 | 14/15，93.33% | 4 | 41 /11 | 61.81s |

三论文保留Flamingo downstream_capabilities不足，最终结构引用违规0；两次VLM调用0。已审核覆盖不等于事实准确率。双论文不是BLIP-2 /Flamingo，也不是后续G2主题的成功重试；受控Fake Smoke只验证wiring，不进入真实结果表。

## 3. F2：90题AI参考口径

| 类型 | 样本 | 通过 | 不通过 |
| --- | ---: | ---: | ---: |
| 单论文Claim | 30 | 26 | 4（3基础设施无有效预测、1有答案但支持不完整） |
| Reviewer正负例 | 30 | 30 | 0 |
| 跨论文比较 | 15 | **8** | **7** |
| 边界 /拒答决策 | 15 | 15 | 0 |
| 合计 | 90 | **79，87.78%** | 11 |

`/metrics/overall/accuracy`字段是混合任务AI参考接受口径，不是独立人工事实准确率。90条AI审核参考、0人工Gold；开放答案AI判定42条（27有效Claim +15比较），无有效输出3条保留失败，不剔除来抬高通过率。结构化标签/拒答决策45条与开放语义评分不能描述成90次独立人工打分。

Reviewer混淆矩阵为TP15 /TN15 /FP0 /FN0，分母30；这是给定Claim/Evidence的固定正负例标签一致性，不能外推生产研究Claim的错误接受率为0。F3的七题原Claim被Reviewer通过却仍被AI质量判为不合格，是不同任务分布和评价范围。

边界15题包含**13不可答、2可答对照**；15/15是拒答/作答决策符合参考，不能写成15次正确拒答。执行模式为57完整研究链、30直接Reviewer、3caption-only，无统一三系统公平比较。

### 比较质量轴不等于整题通过

| AI分项（每题0..2，15题满分30） | 得分 /满分 |
| --- | ---: |
| 事实准确性 | 26/30 |
| 差异正确性 | 26/30 |
| 证据支持性 | 25/30 |
| 范围完整性 | **23/30** |
| 四轴合计 | 100/120，83.33% |

该分项平均与整题8/15（53.33%）并不矛盾。[评分器](../../eval/research_quality/scoring.py)读取哈希绑定的semantic_correct，不从轴平均或阈值自动推导整体正确。比较最突出的低分轴是范围完整性；这不证明其他维度已由人审确认。

成本：456次Provider物理尝试、107工具、1个需求触发Recovery；863.998秒为案例耗时之和，不包含初始化、评分和归档，不是整个项目任务耗时。run_manifest的grading=not_yet_graded是创建时快照，最终AI assessments与score-all已存在且数量一致；G3不改历史字段。

## 4. F3诊断与F3.1：从风险到可选覆盖契约

七题来自F2比较失败，主要风险为跨论文事实混入本论文、关键接口或模型家族遗漏、多阶段覆盖不全、局部检索不足推出全文否定、无关性能范围漂移。只读诊断发现首次可定位质量风险在Worker Claim；14个Claim曾被生产Reviewer通过，Recovery0、7份报告completed。没有将“报告能生成”当作七题成功，也未发现Synthesis新增技术事实或伪造引用。

F3诊断仅运行12项导出相关测试，没有新真实运行，当轮完整测试未重跑；其名称是七题分析的阶段简称。可复核源仍是提交的F2 executions，审计摘要保留本地诊断SHA与结论。

F3.1引入covered /missing /not_applicable和阶段、来源摘录契约。Controller先拒绝覆盖不完整的supported，并反馈invalid_arguments /requirement_coverage_incomplete；Policy在剩余预算内决定补充检索，或显式提交insufficient_evidence。预算耗尽仍是execution_failed，不会自动转换成证据不足。部分事实留在sidecar，不直接进入综合。955项历史工程回归与4条合成轨迹只能证明提交、门槛和失败路径，不证明对真实数据的质量收益。

## 5. F3.1-E：七题质量与成本权衡

原F2严格标签0/7，Coverage实验严格可接受**4/7**。五题有局部改善（接口题仅部分），两题混合退化；“改善5”不能读成“通过5”。

| 口径 | 原基线 | Coverage实验 |
| --- | ---: | ---: |
| 原严格判定 | 0/7 | 4/7 |
| 仅核心事实的敏感性口径 | 3/7 | 4/7 |
| 独立AI必要事实完整需求 | 9/14 | 11/14 |
| 自报结构完整 | 不用于该门槛比较 | 13/14 |
| completed Worker | 14 | 13（另1 failed） |
| 已审核需求 | 14 | 13 |
| 结构比较数 | 7 | 6 |
| completed报告 | 7 | 7 |

一题因多阶段覆盖提交字段混淆后非法工具JSON失败；一题丢失原基线已有生成损失区别并新增证据未明确支持的更新推断。训练推断支持不足不等于已经证明事实错误；8.7% /54×性能数字有来源，问题是范围漂移，不能称数字幻觉。

第1/3/4/7题保留争议敏感性，原F2标签未修改。第4题后处理六维factual_accuracy修订2→1有前版备份，严格失败和整体4/7不变。全部质量判断为非盲AI辅助，独立人工0。

| 正式七题成本 | 基线 | 实验 | 变化 |
| --- | ---: | ---: | ---: |
| 模型调用 | 85 | 101 | +18.82% |
| 工具调用 | 26 | 29 | +11.54% |
| 内层案例耗时总和 | 93.528s | 160.709s | +71.83% |

基线采用F2 detail.metrics.wall_time_ms，而非外层latency_ms；前后仍有计时边界、运行日期/服务状态混杂，不能精确归因全部延迟于Coverage。101次实际尝试含100成功、1非法JSON错误；另工程冒烟7次模型/2次工具单列，项目108次不作为正式比较分母。正式Recovery0 /VLM0，不能称真实恢复能力提升。

## 6. G2 /G2.1：协议与研究完成度分开

| 指标 | G2 | G2.1 |
| --- | ---: | ---: |
| Planner维度 /需求 | 6 /12 | 7 /14 |
| Worker多动作 | 4 | 0 |
| Worker参数错误 | 8 | 0 |
| Worker决策 | 24 | 12 |
| 正常结束Worker | 1/2 | 2/2 |
| execution_failed需求 | 6 | 0 |
| 合法resolved，含证据不足 | 6/12 | 14/14 |
| 已审核需求 | 5/12 | 4/14 |
| insufficient需求 | 1 | 10 |
| 跨论文比较 | 0 | 0 |
| 全链LLM /工具 | 31 /9 | 19 /8 |
| 含预检总耗时 | 41.96s | 52.69s |

问题、论文、模型、主要配置和索引一致，实际计划不同。合法终态率50%→100%只是完成/不足状态均合法结束，不是正确率；覆盖率41.67%→28.57%的分母和需求内容也不同，不能作成对语义质量比较。

G2.1仅在新入口使用原Worker加协议适配，不改冻结Controller或启用Coverage。实测没有Worker错误反馈，预防性约束执行合法；反馈恢复能力只由Fake证明。Synthesis有1次被拦截引用集合错误、1次纠正，最终结构合法，不是最终幻觉率。

LLM调用-38.71%、工具-11.11%，总耗时+25.58%。G2.1的新计划含论文专属轴，却对全部论文展开，4条审核Claim分布在不同维度，无法生成合法比较；Flamingo一次检索后保留大量不足，合理性尚未独立审查。本阶段没有调Planner或重跑挑选结果。

## 7. 可以支持和不能支持的结论

可以支持：Prepared Evidence来源和限定检索可追踪；审核和输出准入能拒绝结构越界；有限协议纠正与恢复有可核验证据；真实失败能被记录，部分协议风险在一次修复验证中消失。

不能支持：79/90是人审准确率；4/7覆盖优化具有普遍收益；G2.1合法结束等于研究正确；所有completed报告均完成比较；图像展示证明像素推理；不同实验的模型调用、通过题数或样本分母可以累加出一个新Benchmark。

保留不同统计边界和历史失败比追求单一“好看”分数更有解释力。原始来源与数字对照清单见[G3来源审计](../acceptance/stage_g3/experiment_audit.json)，本轮工程验证与文件保护结果独立记录，不把历史测试报告冒充本轮执行。
