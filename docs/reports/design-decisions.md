# 技术决策与工程权衡

本文整理 Stage E 至 G2.1 的实际实现和既有实验结果，说明当前设计为何成立、付出了什么代价，以及哪些结论尚不能外推。内容核对日期为 **2026-10-10**；本阶段没有调用真实 Provider，也没有调整提示词、检索或模型能力。

这里的“审核通过”指生产 Reviewer 的模型判定，`VerifiedClaim` 是程序中的准入状态，**不等于独立人工确认的论文事实**。F2 和 F3.1-E 的质量判断来自 AI 辅助核对，独立人工事实审核数为 0。完整架构见 [系统总览](../architecture/system-overview.md)，实验口径见 [实验汇总](experiment-summary.md)。

## 1. 按 Planner / Worker / Reviewer / Synthesis 分层

**当前决定。** Planner 生成需要调查的中性维度，程序将维度投影为各论文的研究需求；每个 Worker 只获取本论文证据并提交候选论断；Reviewer 审核精确绑定的论断与引用；Synthesis 组织已获准事实及缺失状态，不重新研究。Recovery 作为受限的定向纠错环节插在审核与综合之间。它不是另一个开放式研究总控。

**收益。** 不同阶段的责任和错误首次出现的位置可以分开检查。Worker 不应代替上层做跨论文比较，Synthesis 不能绕过审核从模型记忆补齐事实。结构化的 `ResearchPlan → WorkerResult → ReviewResult → SynthesisInput → ResearchReport` 保留计划、证据、审核和交付之间的关联。

**代价。** 多阶段增加模型请求、传递上下文和错误处理成本；上游问题会沿着结构传播。G2.1 的 Planner 生成了论文专属维度，原投影仍对每篇论文生成需求，最终没有同维度的双论文 VerifiedClaim。这说明角色分层提高了可诊断性，却不自动保证规划合适或最终比较完成。

**适用边界。** 模型负责研究维度、证据充分性和论断支持等语义判断；程序负责身份、Schema、预算、观察范围和结果绑定。现有研究入口接受已选论文，不是自动发现、下载并研究任意论文的开放闭环；多论文研究还未接入原 API/UI。Prepared 文本与图注是当前研究主要材料，Reviewer 不读取图片像素。

依据：[Planner](../../app/research/planner.py)、[需求投影](../../app/research/requirements.py)、[Worker](../../app/research/worker.py)、[审核验证](../../app/research/review_validation.py)、[综合准入](../../app/research/synthesis_input.py)、[G2.1 实际计划限制](../acceptance/stage_g2_1/acceptance_report.md)。

## 2. 先做确定性引用验证，再做语义审核

**当前决定。** Evidence ID 指向当前 Corpus 的权威记录；Worker 必须引用已观察的本论文证据。Reviewer 的审核与原 Claim、Requirement 和引用精确绑定，综合阶段仅接收 `supported + validation passed + review passed`。重复、冲突或身份不匹配的审核结果不能任意选择一个通过版本。综合输出还要遵守 Claim / Evidence 白名单和原论文、维度、缺失状态。

**收益。** 不依赖模型承诺就能拒绝不存在的 ID、越界论文引用、未经观察的证据和无证据比较；报告能够沿 Evidence ID 回到论文、页码和原文。即使综合模型提交非法引用，校验器也能拒绝该输出，而不直接交付为成功报告。

**代价。** 严格绑定要求保留观察账本与原记录；记录缺失、审核冲突或依赖无法验证时采用 fail-closed，会减少可交付事实。结构校验自身不能判断一段引文是否支持论断的全部含义，仍需 Reviewer 或独立语义核对。

**适用边界。** `CitationValidator` 校验的是引用身份与白名单子集；研究审核还校验来源和绑定；这些都不构成语义蕴含证明。F2 已出现“ID 真实、生产 Reviewer 通过，但引用没有覆盖附加断言”的案例。G2.1 综合首轮引用集合不合法被拦截，纠正后最终结构合法，也不能被解释为最终事实已人工核实。

依据：[引用身份校验](../../app/evidence/citations.py)、[研究审核绑定](../../app/research/review_validation.py)、[综合输入](../../app/research/synthesis_input.py)、[综合输出校验](../../app/research/synthesis_validator.py)、[F2 支持不完整案例](../acceptance/quality_end_to_end/report.md)、[G2.1 验收](../acceptance/stage_g2_1/acceptance_report.md)。

## 3. Recovery 有界、定向且必须重新审核

**当前决定。** 最多一轮研究 Recovery，只处理精确绑定、语义审核拒绝且具有合法反馈、可用语料和剩余预算的需求。初始审核通过结果不重做；历史单独保存；新 Claim 必须重新通过 Reviewer，不能仅因为执行了恢复就进入报告。

**收益。** 把额外调用集中在已确认的支持问题上，避免无限研究、反复覆盖历史或绕过审核。再次拒绝、没有恢复资格及预算耗尽均有显式停止路径。

**代价。** Recovery 不负责补救所有失败。合法 `insufficient_evidence`、Worker `execution_failed`、Provider 不可用或反馈身份不合法，不会被自动转成恢复机会。G2 的协议耗尽与 G2.1 的大量证据不足因此不能靠原 Recovery 自动填补。

**适用边界。** 必须区分三类机制：Provider transient retry 处理基础设施故障；模型协议纠正处理非法动作或结构；研究 Recovery 处理符合条件的语义审核拒绝。它们的预算与资格不同，不能混作“系统会一直重试到成功”。F3.1-E 的七题实际 Recovery 尝试为 0；G2、G2.1 也正确跳过，不可将这些运行宣传为真实恢复成功证据。

依据：[恢复资格](../../app/research/recovery_eligibility.py)、[恢复运行时](../../app/research/recovery_orchestrator.py)、[历史与结果合并](../../app/research/recovery_merge.py)、[F3.1-E 实测](../acceptance/stage_f3_1_e/acceptance_report.md)、[G2.1 实测](../acceptance/stage_g2_1/acceptance_report.md)。

## 4. F3.1 Coverage Worker 保留为显式实验版本

**当前决定。** 默认入口继续使用 `PaperResearchWorker`。`CoverageAwarePaperWorker` 仅在独立工厂中显式启用，复用原有有界循环、资源槽、引用验证、审核与综合接口；覆盖信息记录在诊断 sidecar 中。缺口存在时不能把部分事实直接升级为正式 `supported`。

**收益。** `covered / missing / not_applicable` 和阶段信息使遗漏可见，减少把一个局部事实误认成整个需求完成的风险。隔离实验版本保留冻结基线和回归可比性，不让尚未充分验证的行为影响所有入口。

**代价。** 更复杂的提交契约增加协议负担和调用成本，结构上的 `covered` 仍可能不准确。F3.1-E 七条已知失败样本严格可接受为 **4/7**；正式模型调用由 **85 增至 101**。同时出现输出协议执行失败和原基线已有事实的遗漏，不能仅据局部改善宣布默认替换成功。

**适用边界。** 七题是已知失败诊断集，不是未见数据；质量评分为 AI 辅助、非盲评。自报覆盖不等于语义完整，Reviewer passed 不等于需求完整，报告 completed 不等于比较正确。原接口不能在保留不足状态时直接向 Synthesis 交付部分已支持事实，这也是实验版本的表达代价。

依据：[覆盖实现](../../app/research/coverage_worker.py)、[显式实验工厂](../../eval/research_quality/coverage_execution.py)、[F3.1-E 真实对照](../acceptance/stage_f3_1_e/acceptance_report.md)。[早期覆盖设计说明](../worker-coverage.md) 中“未运行真实对照”仅适用于 F3.1 当时的开发阶段，不是后续 F3.1-E 的现状。

## 5. G2.1 用协议适配层保留冻结 Controller

**当前决定。** 通过 `WorkerProtocolAdapter` 克隆模型请求，按真实 Schema 和当前 Worker 身份澄清单 function call、检索 `paper_ids` 数组和单论文范围，并给最近一次错误提供最小合法示例。模型响应原样交给旧 Controller：不截断多个 actions、不改名字段、不代选论文、不自动执行或重试。原预算、取消、超时和纠正上限不变。

**收益。** 修正模型所见契约与执行约束之间的含糊之处，同时保持 Controller 严格校验及冻结文件指纹。批量 `resolve_requirements` 明确表示一个调用中的多条结果，不授权同一响应出现多个 function calls。

**代价。** 增加一个需要与真实 Schema 保持同步的适配层；旧入口仍可重放原协议，修复不会自动传播到全部脚本或 API。参数错误仍受原模型调用预算限制，不能把所有参数纠正都误计入独立多动作纠正次数。

**适用边界。** 新入口显式使用原默认 Worker 加适配层，没有切换到 Coverage Worker。唯一真实 G2.1 验证中多动作违规和参数错误都为 0，但没有触发新增错误反馈；其自我纠正路径由 Fake 测试证明，真实结果不能支持“模型依赖新反馈成功恢复”的说法。Planner 计划不同，也不能据合法终态增加推断研究语义质量提升。

依据：[G2 原始诊断](../acceptance/stage_g2_1/diagnosis.md)、[协议适配器](../../app/research/worker_protocol.py)、[显式装配](../../app/research/protocol_runtime.py)、[新验证入口](../../scripts/run_worker_protocol_demo.py)、[G2.1 一次真实结果](../acceptance/stage_g2_1/acceptance_report.md)。

## 6. 停止继续优化固定评测集，保留失败与限制

**当前决定。** 本阶段只审计已有结果，不修改参考标签、题目、Prompt、Corpus、排序或模型，不新跑 Provider，不从一次失败后反复调整并挑选最好结果。既有七题、G2 与 G2.1 原始产物保留，允许后续复核争议标签与首次错误位置。

**收益。** 防止把已知题优化当作泛化能力，保留独立追溯和公平解释的可能性。性能数字只在相同样本和清晰口径下解释，不把工程通过、结构合法、引用身份有效、AI 语义评分四项替换成独立人工事实审核。

**代价。** 固定集中的已知遗漏、规划问题、Provider 波动和协议风险仍然存在；项目展示必须承认未完成比较的运行，而不能给出全能研究系统的印象。没有独立人工 Gold 和新保留集时，不能给出可靠的未知任务质量结论。

**适用边界。** F2 是 90 条混合模块任务，F3.1-E 是其中七条已知失败样本，G2 / G2.1 是一次同主题、不同实际 Planner 计划的演示。三者不能汇成一个新准确率；调用次数与墙钟耗时还需保留计时边界及失败尝试口径。未来若开展质量研究，应另立明确协议、独立数据与审核记录，不覆盖当前历史。

依据：[F2 数据与评分来源](../acceptance/quality_end_to_end/report.md)、[F3.1-E 混杂因素与限制](../acceptance/stage_f3_1_e/acceptance_report.md)、[G2 / G2.1 对比](../demos/blip2-vs-flamingo/g2-1-comparison.md)。

## 决策之间的关系

上述设计共同约束的是**可以交付什么，以及失败后如何保留事实和边界**。模型仍决定研究内容和证据是否足够，Controller 不代替它完成语义任务；程序可以确定性地拒绝越界身份、非法动作和错误绑定，却不能保证模型不会遗漏或误解论文。该边界是保留实验性组件、独立验证与诚实失败记录的原因。
