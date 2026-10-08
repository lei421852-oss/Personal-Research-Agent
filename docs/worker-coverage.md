# Stage F3.1：单论文 Worker 的需求覆盖协议

本阶段增加显式启用的 `CoverageAwarePaperWorker`，保留 Stage E 的全部冻结文件。它正常继承 `PaperResearchWorker`，复用同一个有限循环、异步适配器、信号量、超时、重试、引用校验与取消行为，没有复制执行循环或替换模块全局变量。

原 Stage E/F2 脚本与默认生产入口仍创建旧 Worker。新版本仅在调用方明确构造 `app.research.coverage_worker.CoverageAwarePaperWorker` 时启用。替换冻结默认入口属于冻结冲突，本阶段不做。

## 本地范围与运行输入

权威 `WorkerTask`、`EvidenceRequirement` 的论文、需求 ID、问题与维度保持原样，以维持 Reviewer、Recovery、Synthesis 的精确绑定。决策上下文增加本论文的 `coverage_assignments`：本地目标、当前维度、通用属性、阶段属性、上轮覆盖声明。原问题保留用于理解用户约束，不授权 Worker 自己报告同伴论文事实或做全局比较。

正常组合的 `CoveragePolicyAdapter` 给现有 system instruction 追加通用协议，并把请求委托原 `AsyncLLMAdapter`；预算参数、物理调用统计、资源槽及清理全部原样委托。没有新增工具或额外的自动模型调用。

五类通用属性：

| 维度 | 属性 |
|---|---|
| `vision_encoder` | `type`、`configuration`、`feature_output` |
| `vision_language_bridge` | `connector`、`visual_transform`、`language_interface` |
| `frozen_trainable_scope` | `training_stages`、`frozen_modules`、`updated_modules` |
| `training_objective` | `training_stages`、`losses`、`optimization_targets` |
| `language_model` | `model_family`、`architecture`、`update_state` |

未知维度使用 `requested_facts`，不根据论文名或查询答案硬编码分类。上述方向不预设论文必然有特定模型、架构或阶段；具体属性支持性与适用性由 Policy 根据观察证据判断。

## 覆盖动作与完成门槛

新 Worker 的本地 `resolve_requirement` 输入为 `requirement_id / status / coverage / stages`；批量动作仍叫 `resolve_requirements`。它们是 Worker 本地动作协议，没有改变底层检索工具契约。

每个属性必须是 `covered`、`missing` 或 `not_applicable`：

- `covered`：包含本论文、当前维度的事实声明及已观察的真实引文。
- `missing`：没有事实，不用模型知识补写；缺口必须保留。
- `not_applicable`：包含正向来源依据及引用原文。没有搜到不是不适用。

事实声明携带 `subject_paper_id / dimension_id / text / evidence_ids`。显式错误主体、错维度、未知或未观察证据被拒绝。Policy 禁止范围漂移、无关性能和局部检索推出全文否定；排他与限定缺失声明须携带源引句，程序验证引句在所引证据中真实存在。没有简单封禁 `only` 等词。

阶段敏感维度需要声明证据中识别的阶段，并为各阶段分别报告冻结/更新模块或损失/优化对象。阶段清单的识别是语义任务，Controller 不通过关键词猜测论文有几阶段；若声明阶段清单已覆盖却不提供阶段明细，提交会被拒绝。已列出的第一阶段缺口不能被第二阶段覆盖掩盖。

`supported` 前，所有必要属性及阶段属性必须无缺口。存在缺口时返回现有 `invalid_arguments` 的可恢复 Observation，payload 用 `coverage_issue=requirement_coverage_incomplete` 标明原因及缺失属性。不会执行检索、强制终止或私下选择下一动作；模型可以在原预算内继续调查或明确结束。

正式 Claim 从覆盖事实确定性组装，引用取所有覆盖依据的并集，再调用父类原 `_resolve()` 完成引用、观察范围和恢复绑定校验。不接受可以额外夹带无关段落的自由 `claim` 字段。批量提交先完整校验，全批满足条件才更新正式状态。

## 状态与旧接口兼容

没有新增 `RequirementStatus`，也没有把不完整需求映射成 `supported`：

| 情形 | 原有正式状态 | 部分事实与缺口 |
|---|---|---|
| 覆盖完整且原引用校验通过 | `supported` | 可交给原 Reviewer 审核 |
| 当前证据不足，Policy 明确结束 | `insufficient_evidence` | 正式 Claim/IDs 清空；仅留覆盖 sidecar |
| 模型或工具预算耗尽 | `execution_failed` | 不伪装成证据不足 |
| 取消 | `cancelled` | 原取消语义与已完成需求保留 |

`WorkerResult` 的旧序列化结构保持不变。覆盖信息放在已有 Trace 的扩展字典和 `last_coverage_report`，不把新字段直接塞入旧模型。部分事实未经原 Reviewer 通过，不进入 Synthesis 的 VerifiedClaim。原 Synthesis 不原生表达“部分已支持事实”，本阶段不改变它。

旧 Scripted Policy 的自由 Claim 动作不能直接用于新覆盖 Worker，必须迁移到结构化覆盖动作；旧 Worker 和旧测试保持原行为。调用方需显式选择版本，不隐式降级回自由 Claim。

## 可观测性与成本

公开轨迹保存每次覆盖提交、是否接受、属性状态、阶段缺口及最终正式状态，不保存隐藏推理。`last_coverage_report.metrics` 记录覆盖拒绝数及首次覆盖拒绝之后实际发生的模型/工具调用增量。

这些额外调用是观察到的成本，不证明每一次都必然用于补证据。共享检索按真实 Worker 计数，不将同一调用重复分摊到各需求。一次覆盖拒绝只消耗既有模型决策预算，不消耗未执行工具预算；原多动作协议纠正预算继续独立生效。

## 七题对照准备

运行下面的准备入口只生成请求与基线 Manifest：

```bash
python -m scripts.prepare_worker_coverage_comparison \
  --output artifacts/worker-coverage-comparison-f3-1
```

样本从原七题诊断动态选择，不按论文名称硬编码。`requests.jsonl` 只包含原 `case_id / question / paper_ids / dimension`，不包含参考答案、评分备注、期望 Evidence ID、旧 Claim 或画像。文件指纹、原配置、语料快照只保存在离线 Manifest 中，不是模型输入。

`comparison_worker_tasks(request)` 构造与 F2 同身份的任务；显式的新 Worker 可作为原 `AsyncResearchOrchestrator` 的工厂返回值。原 Reviewer/Recovery/Synthesis 库可消费正式 WorkerResult，完整真实对照链的运行入口尚未切换或执行。

`summarize_coverage_comparison()` 区分属性覆盖率、完整需求率、实际成本，以及需独立语义标注的附加断言/过早完成/最终比较质量。没有语义标注时后三者为 null，并给出零标注分母。结构化覆盖不等于语义正确率，Fake 测试不表示真实研究质量改善。

## 已知边界

模型可能错误自报 `covered`、阶段清单完整或 N/A；声明主体正确也不能证明自然语言没有隐含同伴事实。程序验证结构、来源身份与引用关系，不宣称完全检测语义幻觉。精确引句的存在不等于引句语义上支持排他范围。

没有改变 Retriever、索引、嵌入模型、语料或评测标签；没有运行真实 Provider 对照。尚不能证明 F2 的七题质量提高，也不把声明覆盖率当作新的研究正确率。
