# 五项工程亮点与实现边界

这份说明用于快速理解项目的工程取舍。每项亮点都按“问题 → 机制 → 实现与验证 → 局限”展开，依据当前源码和已保存的实验记录，区分能执行、结构合法、模型审核通过和独立人工事实核验。

项目定位是：**面向已预处理论文语料、具有可追溯证据和有界执行的多论文研究原型。** 当前 Prepared Corpus 为 5 篇论文、46 条文本和 5 条 Figure Evidence；论文元数据目录中的 25 篇候选不等于 25 篇都具有正文证据。图片展示、可选 VLM 工具和已经完成的文本／图注研究评测各有独立边界。

## 1. 将研究决策、单论文调查、审核与报告生成分层

**问题。** 一个模型同时规划、检索、判断引用并写比较报告，难以定位错误，也容易把“报告生成完成”混同于“研究需求有充分证据”。跨论文研究还需要避免某篇论文的 Worker 借用同伴论文事实。

**机制。** 系统以 `ResearchPlan` 表达研究维度，通过需求投影生成论文 × 维度的 `EvidenceRequirement`，按论文分组为 `WorkerTask`。并发 Worker 在各自论文范围内调查；确定性验证与 Reviewer 审核随后执行。只有符合准入条件的 `VerifiedClaim` 进入 Synthesis，缺失单元作为独立状态保留。模型决定证据是否充分及下一动作，Controller 验证动作、范围、预算和引用，不按论文关键词替模型选检索范围。

工作台复用原默认 `PaperResearchWorker`，显式组合 G2.1 的协议适配器，没有启用实验性的 Coverage Worker，也没有复制一套新的研究循环。

**实现与验证。**

| 连接点 | 源码与职责 |
| --- | --- |
| 计划与需求 | [planner.py](../../app/research/planner.py)、[requirements.py](../../app/research/requirements.py)：计划生成、稳定需求投影与任务分组。 |
| 并发研究 | [orchestrator.py](../../app/research/orchestrator.py)、[worker.py](../../app/research/worker.py)：Worker 编排、逐步决策、单论文 scope 与需求结果。 |
| 审核与综合 | [review_runtime.py](../../app/research/review_runtime.py)、[synthesis_input.py](../../app/research/synthesis_input.py)、[synthesis_runtime.py](../../app/research/synthesis_runtime.py)：审核、事实准入、报告及降级。 |
| 工作台组合入口 | [workbench_runtime.py](../../app/research/workbench_runtime.py)、[protocol_runtime.py](../../app/research/protocol_runtime.py)：真实 Planner 和既有研究链。 |

[G3 实验汇总](../reports/experiment-summary.md)记录的 Stage E 双论文真实案例是 **BLIP-2 / LLaVA**：2 个需求获得审核通过，报告生成 1 个结构合法的比较。该案例是工程链和审核结果的证据，不是后续 BLIP-2 / Flamingo 演示的成功重试。[G4.1 验收](../acceptance/stage_g4_1/acceptance_report.md)则通过确定性集成、实际服务启动和真实历史回放验证工作台接入；当轮没有新 Provider 研究。

**局限。** 分层提供职责边界和错误定位位置，不自动解决语义遗漏。G2.1 的 Planner 生成了论文专属维度，但需求仍投影到全部论文，导致同维度缺乏双侧证据、最终比较数为 0。`completed` 只代表对应执行或报告生成合法结束，不保证研究主题已回答完整。该问题保留在报告中，没有为了展示效果改写计划或结果。

## 2. 分开处理引用身份验证与模型语义审核

**问题。** 模型可能提交不存在的 Evidence ID、未观察过的证据、另一篇论文的引用，或引用真实证据却提出它并不支持的论断。单一“引用通过”标签无法表达这些不同风险。

**机制。** 引用首先经过程序检查：Evidence ID 在当前 Corpus 中存在，属于当前论文，出现在 Worker 观察账本中，并精确绑定当前需求和 Claim。Reviewer 随后依据实际提供的证据进行模型语义审核。SynthesisInputBuilder 再次检查 `supported + validation passed + review passed` 以及身份绑定；缺失、重复或冲突审核结果采用 fail-closed，拒绝 Claim 正文不进入事实输入。

综合模型主要选择章节与已有 Claim。权威 Claim、引用和缺失状态由程序组装；输出验证器检查 Claim／Evidence 白名单、论文与维度对应关系以及比较双方的证据。结构合法和模型语义审核通过仍分别展示，不升级为人工事实核验。

**实现与验证。**

| 边界 | 源码与验证材料 |
| --- | --- |
| 证据解析与引用集合 | [resolver.py](../../app/evidence/resolver.py)、[citations.py](../../app/evidence/citations.py)。 |
| 审核前身份和观察账本 | [review_validation.py](../../app/research/review_validation.py)：只把合法、同论文且已观察的证据投影给 Reviewer，不传文件路径。 |
| 精确审核绑定 | [synthesis_input.py](../../app/research/synthesis_input.py)：要求 Worker Task、Requirement、Paper、Claim 正文和 Evidence ID 列表与审核记录一致。 |
| 综合输出准入 | [synthesis_validator.py](../../app/research/synthesis_validator.py)：阻断未知引用、缺少比较一方证据及缺失状态升级。 |

[G2.1 原始统计](../acceptance/stage_g2_1/comparison_summary.json)和[G3 汇总](../reports/experiment-summary.md)记录了 Synthesis 的一次引用集合错误被拦截、随后在有界纠正后生成结构合法报告。这个计数不能称为“最终报告出现一次伪引用”。F2 的七条失败比较里，14 个原 Claim 曾被生产 Reviewer 通过，仍未满足 AI 辅助的完整研究质量口径，说明身份验证与模型审核都不能替代独立质量评估。

**局限。** Evidence ID 真正存在，不代表它支持每个自然语言断言；模型 Reviewer 可能遗漏范围、完整性或推断问题。Reviewer 使用文本与图注投影，不读取图片像素。现有 F2 参考集经 AI 辅助审核，独立人工审核和正式人工 Gold 数量均为 0，不能宣称“消除了幻觉”或“所有结论已由人审确认”。

## 3. 用预算、资源槽与分层恢复控制失败范围

**问题。** 真实调用会遇到模型多动作、参数错误、Provider 瞬时故障、阻塞检索和取消。如果把它们统一处理为无限重试，可能增加费用、重复后台请求，或在程序显示取消后仍继续占用资源。

**机制。** 系统区分三个恢复层次：

1. **基础设施重试**：RetryPolicy 根据实际依赖错误分类，只对符合条件的瞬时故障有界重试。
2. **Policy 协议纠正**：多动作响应拒绝全部动作，不执行其中第一个；将错误反馈交给下一轮模型，消耗模型／决策预算，不消耗实际工具预算。
3. **研究定向 Recovery**：仅对满足资格的语义审核拒绝需求再调查一轮，保留初始历史，新 Claim 必须重新接受 Reviewer。

Worker 槽与 LLM／VLM／检索槽分开。同步接口集中 offload，已启动线程在返回前保留资源槽；外层取消不能强杀 Python 线程。运行时在途调用收尾后 `drain`，避免释放资源过早或者因等待超时叠加后台重试。

G2.1 的 `WorkerProtocolAdapter` 只修改发送给模型的请求副本：提供准确的单动作约束、单论文 `paper_ids` 数组 Schema 和参数纠正示例。原模型响应、原 Worker、原 Controller 的校验继续保留；适配器不截断多动作，不将 `paper_id` 静默改名，不自动执行下一动作。该适配只存在于显式的新研究入口，不代表旧入口已被改写。

**实现与验证。**

| 机制 | 当前实现 |
| --- | --- |
| 资源与 Worker 预算 | [runtime.py](../../app/research/runtime.py)：默认 Worker／LLM／VLM／检索槽为 3／2／1／1；单 Worker 模型 20、工具 8、协议纠正 2。 |
| 依赖与线程边界 | [async_tools.py](../../app/research/async_tools.py)、[retry.py](../../app/research/retry.py)：有界重试、超时、资源持有与在途收尾。 |
| 协议适配 | [worker_protocol.py](../../app/research/worker_protocol.py)：明确字段及动作契约，完整转发响应与预算。 |
| 一轮研究恢复 | [recovery_eligibility.py](../../app/research/recovery_eligibility.py)、[recovery_orchestrator.py](../../app/research/recovery_orchestrator.py)：资格、恢复预算、重新审核与历史保留。 |
| 工作台全请求控制 | [run_manager.py](../../app/research/run_manager.py)、[workbench_runtime.py](../../app/research/workbench_runtime.py)：运行 1、排队 2、历史最多 20 条／1 小时；默认整体模型预算最多 200，实际 Provider 尝试开始前扣减，包括重试和 VLM。 |

[G2 / G2.1 汇总](../reports/experiment-summary.md)中 Worker 多动作错误为 **4→0**，参数错误为 **8→0**。G2.1 真实验证未触发 Worker 错误反馈，不能据此声称“真实模型多次成功自纠错”；纠正路径由 Fake 确定性测试验证。[G4.1 工程验收](../acceptance/stage_g4_1/acceptance_report.md)进一步覆盖队列、重复提交、预算、真实取消与在途 drain。

**局限。** 预算用于控制失败范围，不判断语义充分性，也不保证任务成功。420 秒工作台等待超时不是线程强杀上限，取消后的收尾可能增加实际等待。任务与历史保存在单进程内存中，重启会丢失，当前不是生产任务队列或多进程服务。

## 4. 展示可观察执行事实，明确区分 live、derived 与 replay

**问题。** 工作台如果将历史回放标成实时执行、给没有时间记录的阶段补造起止时间，或者公开模型内部推理，会误导使用者并增加敏感信息暴露。

**机制。** RunEvent 明确包含三类来源：

| 来源 | 实际含义 | 时间与成本边界 |
| --- | --- | --- |
| `live` | 本次真实发生的 Planner 生命周期、模型或检索回调。 | 模型返回不等于动作已执行，不等于 Agent 已完成。 |
| `derived` | 从本次返回的 Plan、Worker、Review、Recovery、Report 推导的展示事实。 | 无原始时间时保留 `null`，不制造生命周期。 |
| `replay` | 从固定白名单历史文件加载的记录。 | 本轮不调用 Provider、不检索；历史调用次数不计入本轮成本。 |

公开 DTO 投影动作、校验状态、证据、缺口与成本，不直接序列化全部领域对象或 SDK 响应。Validator 卡片展示原 `validation_status` 汇总；它没有独立生命周期回调，因此不显示虚构的真实启动／结束。Evidence 以报告快照和当前 Corpus 核对状态分别呈现，模型审核与人工审核状态分开。

**实现与验证。** [run_events.py](../../app/research/run_events.py)定义事件来源并递归删除私有字段；[run_presenter.py](../../app/research/run_presenter.py)投影实际结果；[run_replay.py](../../app/research/run_replay.py)限制历史文件来源并记录来源哈希，发布验收再与原清单核对；[工作台架构](../architecture/workbench.md)说明游标、状态与取消语义。

[G4.1 验收](../acceptance/stage_g4_1/acceptance_report.md)记录 **1,155 项工程测试通过**、8 项浏览器历史回放检查通过，以及实际启动和正常退出。回放保留 G2 的 5 个已审核论断／7 个缺失单元、G2.1 的 4 个已审核论断／10 个缺失单元，没有把它们包装成完整比较成功。当轮新 Provider、Embedding 和检索调用均为 0。

**局限。** 并非所有内部阶段都有实时生命周期，部分状态只能从结果派生。历史来源完整和页面展示正确，不是本轮再次验证研究质量。图片证据可展示、可选 VLM 工具存在，也不能证明本阶段完成了像素级视觉推理验证。UI 的配置就绪只说明只读预检条件满足，不代表 Provider 网络连通已经验证。

## 5. 让实验结果决定采用范围，而非只展示成功案例

**问题。** 将不同任务集的通过率、模型审核结果和工程终态率合成一个“准确率”，或针对已知失败题反复优化，会掩盖系统的代价和剩余能力边界。

**机制。** 保留冻结基线、原预测和诊断轨迹，分别记录样本身份、运行配置、评分来源和计时边界。完整工程测试、结构合法、引用身份合法、AI 辅助语义判断、独立人工核验是不同层次。实验性 Coverage Worker 只由独立入口显式启用，已有成本／质量权衡不足以支持默认替换原 Worker。

**实现与验证。**

| 实验 | 可支持的结论 | 不能外推的内容 |
| --- | --- | --- |
| F2 的 90 条混合任务 | AI 参考接受 **79/90**；比较任务 **8/15**，范围完整性是明显短板。 | 独立人工审核为 0；不是统一端到端流程的 90 次公平对照，也不是人工准确率。 |
| F3.1-E 七条已知失败 | 严格可接受 **4/7**，并存在遗漏和一次执行失败；模型 **85→101**、工具 **26→29**、案例耗时合计 **93.528→160.709 秒**。 | 不是未知数据泛化能力；计时与 Provider 状态存在混杂，不能将全部延迟精确归因于覆盖机制。 |
| G2 / G2.1 同主题演示 | 协议错误减少，合法终态 **6/12→14/14**。 | Planner 分别生成 6／7 个维度、12／14 个需求；G2.1 只有 4 个已审核需求、10 个不足、比较 0，合法结束不是研究正确率。 |
| G4.1 集成验收 | 工作台接入、公开事实投影和历史回放通过工程检查。 | 未启动新的真实研究，不提供新的语义质量或视觉推理结论。 |

指标、原始记录链接与限制见 [实验汇总](../reports/experiment-summary.md)；设计收益与代价见 [技术决策](../reports/design-decisions.md)；[G3 来源审计](../acceptance/stage_g3/experiment_audit.json)保存来源与核验结果。当前代码中的[coverage_worker.py](../../app/research/coverage_worker.py)和[独立实验入口](../../scripts/run_worker_coverage_comparison.py)继续保持可选，不默认接入工作台。

**局限。** AI 辅助参考和非盲诊断不是独立人工 Gold，也不能证明生产环境可靠。停止固定评测集调优是为了保留实验解释力；未来仍需新样本、明确适用维度和独立人工审核。本次项目发布不以增加通过题数或重新运行实验为目标。

## 展示时可以直接说明的边界

可以展示研究分层、证据来源链、结构化缺失、协议拒绝和恢复、调用预算及真实历史记录；可以解释已经观察到的质量／成本权衡。不能宣称自动理解任意 PDF、所有引用语义正确、全报告经独立人审、多 Agent 必然优于简单 RAG，或已有工程检查证明生产可靠。

