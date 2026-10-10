# 简历项目描述与面试准备

依据当前代码、[G3 技术报告](../reports/technical-report.md)与 [G4.1 验收](../acceptance/stage_g4_1/acceptance_report.md)整理。这里提供可直接使用的项目表述，不代填任职经历、团队规模或公司实习成果。历史实验的样本、口径与限制均保留，不能把 AI 辅助评分写成独立人工审核准确率。

## 1. 可复制的简历项目条目

**项目名称：Multimodal Research Agent｜多论文证据驱动研究智能体**

**技术栈：** Python、asyncio、Pydantic、FastAPI、Chroma、Qwen Embedding、OpenAI-compatible LLM API、原生 HTML/CSS/JavaScript。

**项目简介：** 面向预处理学术论文的证据研究原型，将用户问题转为论文 × 研究维度的需求，由并发 Worker 获取论文范围内的证据，经过引用验证、模型审核和有限恢复，生成保留缺失项的结构化研究报告，并通过 Web 工作台展示真实执行过程。

**核心成果：**

- 建立固定 PDF 版本、文本选区、图注与 Figure 资产的来源契约，支持 5 篇预处理论文、46 条文本和 5 条 Figure Evidence；使用稳定 Evidence ID 将论断回溯到论文、PDF 页码及原文，区分 25 篇元数据目录与实际正文覆盖。
- 实现 Planner → 并发 Worker → Reviewer → 有限 Recovery → Synthesis 研究链路，以单论文检索范围、单动作协议、模型/工具预算及共享资源槽约束执行；将空结果、执行失败、协议拒绝和审核不可用分别反馈并保留。
- 在模型语义审核前后加入确定性引用与结果绑定校验，限定综合阶段仅使用已审核论断；拒绝未经观察或越界的 Evidence ID，保留缺失需求，并在生成失败时输出带真实引用的确定性降级报告。
- 交付可交互 Web 工作台及统一启动入口，展示研究节点、工具动作、证据、缺失项和调用成本，支持取消任务及真实历史回放；G4.1 完整工程回归为 1,155 项通过，并归档真实研究与 AI 辅助质量评测供复核。

**指标说明，可按简历版面选择保留：** 在 90 条混合质量任务中，按 AI 参考与辅助语义评分口径通过 79 条，其中跨论文比较为 8/15；独立人工 Gold 与事实审核数为 0。这组结果用于说明评测与错误分析工作，不能表述为“人审准确率 87.78%”或未知任务上的泛化性能。

**多模态边界：** 系统具备 Figure 资产定位、展示及可选图片分析工具，当前多论文研究与所述质量实验主要基于 Prepared PDF 文本及图注。它们没有验证像素级视觉推理质量，也不具备任意 PDF 自动解析或图像向量检索能力。

事实来源：[研究架构与语料](../reports/technical-report.md)、[实验指标](../reports/experiment-summary.md)、[工作台架构](../architecture/workbench.md)。工程测试数量是 G4.1 已验收值，不代表模型质量评分。

## 2. 不同岗位的强调方式

| 求职方向 | 建议优先强调 | 面试中应主动说明的边界 |
| --- | --- | --- |
| RAG / 检索算法实习 | Evidence 数据契约、论文范围过滤、分层论文发现、独立 Paper/Evidence Index、检索评测与错误来源诊断。 | 使用现有 Embedding + Chroma；没有实现 Hybrid、BM25、RRF 或新的重排序算法。Stage E 的检索变化包含语料扩充，不能归为算法提升。 |
| Agent 应用 / 工程实习 | Policy 与 Controller 分工、Observation 反馈、有界并发、协议纠正、审核准入、失败恢复、API DTO 与事件工作台。 | 完成状态不等于研究正确；当前工作台是单进程本地原型，无分布式队列或持久化研究恢复。 |
| 多模态算法 / 应用实习 | Figure、Caption、Page Context 的联合输入契约，跨模态证据来源和视觉分析工具边界。 | 跨论文质量实验主要验证文本/图注研究；不能将 Figure 展示或这些实验称为像素级视觉推理验证，也没有训练新的 VLM。 |

按岗位调整已有成果的顺序即可。模型论文阅读、个人机制复现、真实公司工作与本项目实验应分别陈述，不能互相替代。

## 3. 一分钟面试介绍

> 我做的是一个面向预处理论文的证据驱动研究智能体。它从单 Agent 的 RAG 工具循环发展到多论文研究：Planner 生成研究维度，每篇论文由一个 Worker 在自己的范围内检索，随后验证引用、审核论断，必要时做一轮定向恢复，最后只用已审核事实综合报告。我的重点是让错误和证据可追踪，而不是只得到一段看起来合理的答案。现在工作台能展示真实动作、来源、缺失项和调用成本。90 条混合任务按 AI 参考口径通过 79 条，但比较题只有 8/15，没有独立人工 Gold。真实演示也暴露了协议和规划问题，所以我保留失败记录并做有限修复。当前研究主要使用文本与图注，不把它包装成通用自主科研或已验证的像素级视觉推理系统。

追问依据：[技术报告](../reports/technical-report.md)、[完整实验口径](../reports/experiment-summary.md)。

## 4. 技术问答

### 1）为什么需要多个角色，不用一次检索后直接生成？

跨论文问题需要分别确认每篇论文在同一维度上的事实，并保留一方证据不足的状态。Planner 拆需求，Worker 调查本论文，Reviewer 审核候选事实，Synthesis 只组织获准事实，能定位错误在哪一阶段首次出现。代价是请求和协议成本增加；简单文本 QA 并不一定适合这条长链。现有项目没有完成公平的三系统全量性能比较，不能声称多 Agent 总是优于单轮 RAG。

依据：[分层决策](../reports/design-decisions.md)、[实验汇总](../reports/experiment-summary.md)。

### 2）Planner 输出什么？论文 × 维度矩阵如何生成？

Planner 输出合法论文集合与研究维度，程序的 `expand_requirements` 对每篇论文、每个维度稳定生成 Requirement ID，再按论文分组为 WorkerTask。该投影确定性但不理解维度是否适用于每篇论文，因此 Planner 应采用中性维度。G2.1 的论文专属维度被展开到全部论文，就是“结构合法、语义规划仍不合适”的真实例子。

依据：[需求投影源码](../../app/research/requirements.py)、[G2.1 前后对照](../demos/blip2-vs-flamingo/g2-1-comparison.md)。

### 3）Worker 如何防止把其他论文事实混入当前论文？

每个 Worker 接收固定的论文身份和需求集合。检索调用必须提供该论文的单元素 `paper_ids` 范围，Controller 校验范围，引用也必须属于本论文并已被实际观察。G2.1 适配器把这一已有约束准确表达给模型，没有根据问题关键词代选论文。身份约束能阻止引用越界，但仍不能完全阻止模型在自然语言论断里混入没有支持的事实，所以还需要语义审核和独立质量核对。

依据：[默认 Worker](../../app/research/worker.py)、[协议视图](../../app/research/worker_protocol.py)、[审核绑定](../../app/research/review_validation.py)。

### 4）LLM Policy 和 Controller 的分界在哪里？

模型决定还缺什么信息、调用哪个合法工具、是否已足够支持需求。Controller 验证 Schema、单动作、论文范围、观察过的证据、预算和引用身份，不判断“证据足够”并强制结束。错误转成 Observation 后再由模型纠正；Controller 不截取多个动作中的第一个，也不静默改参数。

依据：[Worker 决策循环](../../app/research/worker.py)、[协议适配](../../app/research/worker_protocol.py)、[技术报告](../reports/technical-report.md)。

### 5）什么样的 Claim 才能进入最终报告？

必须是 Worker 提交的 `supported`，经确定性验证通过，并具有与当前 Requirement、论文、原始 Claim 和 Evidence ID 精确匹配的 `review passed`。综合输入重新检查这些条件；重复审核、缺失审核或身份冲突不能任意选一个通过结果。审核拒绝的 Claim 正文不会进入综合事实输入，而是保留为缺失状态。

依据：[SynthesisInputBuilder](../../app/research/synthesis_input.py)、[确定性审核验证](../../app/research/review_validation.py)。

### 6）引用身份验证通过，是不是就说明事实正确？

只能说明 Evidence ID 存在、属于允许范围及相应记录。它不能证明引用涵盖论断的全部含义，也不能证明模型没有误读。语义 Reviewer 是第二道模型判断，仍可能漏掉附加断言或需求缺口。独立人工审核属于另一个证据等级，现有 F2 并没有这一级验证。

依据：[CitationValidator](../../app/evidence/citations.py)、[Reviewer](../../app/research/reviewer.py)、[实验口径](../reports/experiment-summary.md)。

### 7）Reviewer 为什么不能保证研究正确？

它审核被提供的具体 Claim 与引用，并不通读整篇论文或保证 Requirement 完整。F2 的比较失败案例中，已有论断通过生产 Reviewer，独立 AI 质量核对却发现范围或完整性不足。固定 30 条 Reviewer 正负例全通过，也不能外推为生产研究场景错误接受率为零。

依据：[Reviewer 输入与指令](../../app/research/reviewer.py)、[F2 混淆矩阵与失败说明](../reports/experiment-summary.md)。

### 8）Recovery、Provider Retry、协议纠正有什么区别？

Provider Retry 处理有限的瞬态连接或服务错误；协议纠正处理多个动作、非法结构等模型契约违规；研究 Recovery 则只针对身份精确绑定、具有合法反馈和剩余预算的语义审核拒绝需求，最多一轮。恢复 Claim 还要重新审核。合法证据不足、执行失败、不可用审核不自动变成研究 Recovery，避免无限调查或绕过审核。

依据：[基础设施重试](../../app/research/retry.py)、[协议控制](../../app/research/worker.py)、[Recovery 资格](../../app/research/recovery_eligibility.py)、[恢复编排](../../app/research/recovery_orchestrator.py)。

### 9）为什么 F3.1 Coverage Worker 不默认启用？

它将通用维度拆成 `covered / missing / not_applicable` 属性及训练阶段，让缺口可见，但新增结构也增加协议负担。七条已知失败样本严格可接受 4/7，正式模型调用 85→101、工具 26→29，同时出现遗漏和执行失败。自报 covered 不等于事实正确，局部收益不足以支持替换默认 Worker，因此只在显式实验入口使用。

依据：[Coverage Worker](../../app/research/coverage_worker.py)、[F3.1-E 对照](../acceptance/stage_f3_1_e/acceptance_report.md)、[默认保留决策](../reports/design-decisions.md)。

### 10）G2.1 到底修了什么，有没有放宽 Controller？

共用检索 Schema 支持全库范围，而单论文 Worker 执行约束要求本论文 scope，模型所见契约有歧义。适配器只克隆请求：明确每轮一个 function call，将检索参数表达为当前论文的单元素 `paper_ids` 数组，并为最近错误给合法结构示例。响应原样交给旧 Controller，不截断多个动作、不接受错误字段、不自动选论文，预算和取消语义也不变。

依据：[G2 原因定位](../acceptance/stage_g2_1/diagnosis.md)、[WorkerProtocolAdapter](../../app/research/worker_protocol.py)、[装配入口](../../app/research/protocol_runtime.py)。

### 11）协议错误变成零，为什么比较结果仍是零？

协议合法只能让执行继续。G2.1 Planner 使用论文专属轴，矩阵投影却要求每篇论文调查全部轴；4 条审核通过的 Claim 没形成同维度的双论文证据对，另外 10 条为合法不足，因而没有可交付比较。G2 与 G2.1 的计划分别是 6/7 维度、12/14 需求，不是同计划质量对照。不能把 14/14 合法终态当成研究正确率或宣称语义质量提升。

依据：[G2.1 真实验收](../acceptance/stage_g2_1/acceptance_report.md)、[原比较摘要](../acceptance/stage_g2_1/comparison_summary.json)。

### 12）如何准确介绍 F2 的 79/90？

它是 90 条混合任务按 AI 参考标签和辅助语义评分口径通过 79 条：事实 26/30、固定 Reviewer 案例 30/30、比较 8/15、边界 15/15。边界含 13 个不可答题与 2 个可答对照；执行方式也混合完整研究链、直接 Reviewer 和图注路径。没有独立人工 Gold，不能称“人审准确率”，更不是多 Agent 对单 Agent RAG 的优势证明。

依据：[F2 原报告](../acceptance/quality_end_to_end/report.md)、[审计汇总](../reports/experiment-summary.md)。

### 13）评测体系中如何区分检索、结构、语义和工程？

检索 Recall 测相关证据或论文是否进入 Top-K；引用校验测身份与范围；AI 语义评估测候选事实、支持性和比较内容；单元/集成测试测预算、并发、失败与接口契约。开放事实和比较评分汇总独立的语义 assessment，不伪装成字符串匹配准确率。现有 F2 及七题诊断为 AI 口径，独立人工审核未完成。

依据：[质量评分器](../../eval/research_quality/scoring.py)、[实验汇总](../reports/experiment-summary.md)、[G4.1 工程验收](../acceptance/stage_g4_1/acceptance_report.md)。

### 14）如何解释七条失败样本对照，避免只展示成功？

使用原 F2 保存的基线，不重跑旧 Worker；同题、同论文、同维度显式启用 Coverage Worker 后走原审核和综合。严格可接受 4/7，但有 5 题局部改善与 2 题混合退化，局部改善不能等于通过。模型和工具成本分别增加约 18.82%、11.54%；耗时也增加且有服务状态混杂。该集合来自已知失败案例，只能用于诊断，不能证明未知数据泛化提升。

依据：[逐题与成本结果](../reports/experiment-summary.md)、[F3.1-E 验收](../acceptance/stage_f3_1_e/acceptance_report.md)。

### 15）Async 并发怎样控制？取消能立即停止 Provider 吗？

Worker 并发与 LLM、VLM、检索资源槽分别限制，阻塞 SDK/检索由适配器 offload。协程取消不能强杀已开始的同步线程，因此保留资源槽到真实调用收尾，再 drain 和关闭客户端，避免取消后重复占用资源。工作台的取消请求与最终 cancelled 分开表示，在途调用仍计入实际成本；这是本地原型的执行边界。

依据：[ResourceGate](../../app/research/runtime.py)、[异步编排](../../app/research/orchestrator.py)、[工作台取消与计数](../architecture/workbench.md)。

### 16）可视化节点是真实执行，还是预设动画？

UI 展示公开事件与结果 DTO：`live` 来自真实回调，`derived` 来自本轮实际结果，`replay` 来自固定历史记录。缺少原始时间就保留 null，不编造阶段起止；引用验证与审核节点部分是逐需求结果汇总，不声称新增实时生命周期。Trace 显示工具、状态、证据和耗时，不输出隐藏思维。G4.1 通过历史回放和工程集成验证，没有新发起真实 Provider 研究。

依据：[事件模型](../../app/research/run_events.py)、[结果投影](../../app/research/run_presenter.py)、[工作台契约](../architecture/workbench.md)、[历史回放](../demos/workbench-replay.md)。

### 17）论文发现索引与研究正文索引为什么分开？

论文级索引用题目、摘要和结构化画像寻找候选论文；证据索引用已准备原文获取可引用材料。25 篇元数据中的发现结果并不保证有正文，当前研究语料只有 5 篇。缺少正文时返回不可用或空结果，不用摘要/画像填充技术事实。工作台当前接收明确选择的 2–3 篇 Prepared 论文，没有自动发现、下载到研究的开放闭环。

依据：[工程清单](../architecture/engineering-inventory.md)、[当前工作台](../architecture/workbench.md)、[证据与数据边界](../reports/technical-report.md)。

### 18）“多模态”体现在哪里？你是否验证了视觉推理？

Evidence 模型同时保存文本与 Figure，工具可定位图片，并在可用视觉 Provider 下将 Figure、Caption、Page Context 送入分析路径。但当前多论文研究和质量评测主要依据正文与图注，Reviewer 只接收文本/图注元数据，不读像素。F3.1-E 和 G2 系列 VLM 调用为零，不能据此声称像素级研究质量已验证。应把已实现的图片工具、历史验证条件与当前研究评测分开介绍。

依据：[能力边界](../reports/technical-report.md)、[历史 Demo 验证状态](../demos/README.md)、[Reviewer 输入](../../app/research/reviewer.py)。

### 19）Synthesis 模型故障时会不会丢掉已有事实？

综合只收到获准事实与缺失状态，输出再经身份、引用与比较约束校验。允许有限协议纠正，失败或预算耗尽后程序用已核验事实与真实引用生成最小降级报告，不加新技术判断，并标记 `generation_degraded`。没有任何 VerifiedClaim 时可以合法输出证据不足报告；取消仍保持取消语义，不能伪装成不足。

依据：[综合输入](../../app/research/synthesis_input.py)、[输出验证](../../app/research/synthesis_validator.py)、[综合运行时](../../app/research/synthesis_runtime.py)。

## 5. 展示与表述检查

面试演示可以先回放 G2/G2.1，并将不完整结果作为设计权衡说明；若另行发起真实研究，应单独记录本轮配置、成本与结果，不能用历史回放冒充新运行。当前启动和回放步骤见 [工作台使用指南](../guides/ui-quickstart.md)。

可使用的表述是“引用可追溯”“有界执行与审核准入”“AI 参考口径评测”“真实失败保留”。以下结论没有现有证据：通用自主科研、任意 PDF 自动研究、人工事实核验 87.78%、零幻觉、Coverage 普遍优于默认 Worker、协议合法等于比较正确、已验证像素级多模态研究质量。

最终项目发布仍应以 [技术报告](../reports/technical-report.md)、[G4.1 验收](../acceptance/stage_g4_1/acceptance_report.md)及后续真实发布检查为准。本材料没有新增模型能力，没有运行新评测或独立完成事实人审。
