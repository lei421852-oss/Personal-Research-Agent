# Stage F3.1 中文验收报告

日期：2026-10-09。分支：`feature/stage-f3-1-worker-coverage`。

## 验收结论

**PASS：显式启用的 F3.1 覆盖 Worker 工程验收通过。** 结论限于新 Worker 的协议、执行与兼容性，不能解释为冻结的默认入口已经切换，或真实七题研究质量已经提高。默认入口替换因冻结约束未实施；原 Stage E/F2 入口保持不变。

完整工程测试 **955 passed，0 failed，1 个既有 Starlette/httpx 弃用提示**。本轮新增 **81 项**：覆盖模型36项、Worker与对照统计45项。原有测试没有删减或改写，既有12项只读诊断测试也在本轮完整套件中通过。工程回归实际耗时13.88秒；没有执行真实 Provider 或90题评测。

## 基线与冻结完整性

- Stage E 标签：`stage-e-freeze-v1`；本地标签未改变。
- F2 基线 HEAD：`263a779f4e2ef3435caea506a3c52ec554702764`。
- **292 个冻结文件全部 SHA256 一致**，包括旧 Worker、Planner、Reviewer、Recovery、Synthesis、检索与相关模型。
- **47 份受保护产物一致**：原 F2 90条执行/预测/评分、候选与AI参考数据、七题原根因诊断等，没有修改标签或参考答案。
- 初始工作区不干净：上一轮七题诊断脚本、测试和报告未提交。本轮保留它们，不清理、不覆盖，也不混入F3.1提交。

完整名单、原分支/HEAD、标签值和指纹见 `baseline.json`；本轮核验见 `integrity.json`。

## 模块与实现

| 新增文件 | 职责 |
|---|---|
| `app/research/coverage_models.py` | 三态覆盖属性、本论文事实、训练阶段和结构化提交 |
| `app/research/coverage_worker.py` | 新 Worker 子类、范围约束、覆盖提交门槛、公开诊断 |
| `eval/research_quality/coverage_comparison.py` | 七题白名单投影、原基线绑定、覆盖/成本与语义分母统计 |
| `scripts/prepare_worker_coverage_comparison.py` | 只准备对照，不运行模型或检索 |
| `scripts/verify_worker_coverage_baseline.py` | 只读核验冻结与原评测产物 |
| `tests/unit/research/test_coverage_models.py` | 36项覆盖结构测试 |
| `tests/unit/research/test_worker_coverage.py` | 45项执行、兼容和统计测试 |
| `docs/worker-coverage.md` | 新旧协议迁移、状态映射、启用与限制说明 |

未修改任何已有生产文件。正常继承 `PaperResearchWorker.run()`，通过 `_context/_schemas/_resolve/_result` 扩展，不复制运行循环，不使用 monkey patch，不替换模块全局。

正常组合适配器追加通用 System Instruction，原 ResourceGate、RetryPolicy、超时、取消、实际调用计数和决策预算原样委托。没有新增 Agent Tool。默认任务配置/生产索引/Embedding/检索排序/语料未改变。

## 完成门槛与兼容性

五类维度分别检查编码器类型/配置/输出、连接/转换/LM接入、训练阶段/冻结/更新、阶段/损失/优化对象、模型家族/架构/更新状态。属性可以为 `covered / missing / not_applicable`，不预设每篇论文必然具有所有属性。N/A需要正向来源及真实引句；未搜到不是N/A。

提交必须逐属性声明同论文、当前维度及已观察引文。存在缺口则不接受完整 `supported`，以原可恢复 `invalid_arguments` 外壳返回 `requirement_coverage_incomplete` 和具体缺口，模型自己决定继续调查或明确不足。拒绝提交不虚增工具调用；原模型硬预算控制重复尝试。

正式 Claim 从覆盖事实确定性组装，引用并集继续经过父类引用校验。没有可自由追加性能段落的 Claim 参数。支持有原文依据的排他表达，没有机械禁用 `only`。批量提交先完整校验，避免半提交。

状态没有新增或暗改：完整覆盖且原校验通过进入 `supported`；不足结束进入 `insufficient_evidence`，正式Claim/引用为空；预算耗尽仍 `execution_failed`；取消继续传播 `CancelledError`。部分事实与阶段缺口只留sidecar，未经Reviewer通过，不送入Synthesis的VerifiedClaim。

权威需求ID、问题、维度保持不变。原Reviewer Validator和SynthesisInputBuilder兼容测试通过；全量Recovery/并发/协议纠正/预算/取消回归通过。覆盖版本地提交契约需要新的结构化动作，旧自由Claim Scripted Policy需迁移；旧 Worker 本身不受影响。

## 四条受控执行证据

`controlled_traces.jsonl` 保存四条实际执行过的 Scripted/Fake 轨迹，仅证明工程机制，证据来自合成测试Fixture，不是原七题真实模型评测。

| 受控路径 | 结果 | 实际工具/模拟模型调用 |
|---|---|---|
| 桥接接口缺口 → 再搜索 → 完成 | `supported` | 2 / 4 |
| 冻结LLM但家族缺失 → 明确不足 | `insufficient_evidence` | 1 / 3 |
| 第一阶段缺口、第二阶段已有事实 → 明确不足 | `insufficient_evidence`，部分事实仅sidecar | 1 / 3 |
| 缺口提交后模型预算耗尽 | `execution_failed` | 1 / 2 |

桥接案例首次覆盖拒绝后新增1次工具调用、2次模拟模型调用；该统计表示观察到的增量，不代表因果必要性或真实成本收益。所有轨迹不保存模型隐藏推理。

## 七题对照准备与指标

`comparison/requests.jsonl` 从原七题动态提取，只包含 `case_id / question / paper_ids / dimension`，没有参考答案、评分备注、期望Evidence ID或旧Claim。对应14个原身份WorkerTask，原问题、语料快照、预算配置与执行记录hash都保存在离线Manifest中。

本轮不运行完整对照链。`comparison_worker_tasks()` 与新Worker类提供显式准备/构造入口；旧默认运行脚本不自动切换。后续完整 Reviewer→Recovery→Synthesis 对照应采用新显式工厂，不替换冻结脚本的模块变量。

指标区分结构性属性覆盖率、完整需求率、实际模型/工具成本及独立语义标注。没有语义标注时，无依据附加断言、真正过早supported、最终比较质量为null，标注分母0。拒绝的supported尝试不称作已经提交的错误完成。

统计器把覆盖记录绑定全部权威需求，缺失/未知需求或重复Worker会报错，避免遗漏需求导致100%覆盖。重复同题语义标注会报错，防止重复分母。只有sidecar汇总时明确声明未绑定Worker结果；成本缺失显式保留，不伪装成0成本。

## 检查记录及未解决边界

- 完整pytest：退出码0，955项通过；记录 `test_results.txt`。
- `ruff check app tests scripts eval`：退出码0；记录 `lint_results.txt`。
- `ruff format --check app tests scripts eval`：退出码0，217个文件已格式化；记录 `formatter_results.txt`。
- 冻结/基线检查：退出码0；记录 `integrity.json`。
- 额外 `ruff format --check .`：退出码1，提示两份既有受保护标注指南Markdown代码块格式；记录 `extra_markdown_formatter_results.txt`。原既有Python检查范围已通过，这两份文档保持原哈希，未为消除提示而改数据。

Controller 能验证结构、论文/维度归属、引用身份与引句存在，不能独立证明自然语言语义蕴含。模型可能错误自报covered或完整阶段清单；正确的subject字段不保证正文没有同伴事实。未知维度使用generic requested_facts，而不是自动猜五类类别。上述属于明确限制，不以关键词封禁或新增Reviewer掩盖。

默认入口切换受到冻结约束，本轮停止该部分；原下游不能原生审核/综合部分事实，本轮也不修改其状态模型。无真实Provider对照，七题质量改善尚未验证。

## 本地 Git 与结束状态

前两项提交：

- `3294cad`：需求范围与覆盖结构。
- `ed7cc44`：Worker覆盖提交门槛及协议说明。

最后本地验收提交包含测试、对照准备、诊断与本报告；其SHA在最终交付时汇报。未执行remote push/pull，未创建新Tag。只提交本轮新文件，保留原未提交诊断文件。

**F3.1 到此停止，等待人工Review；没有启动F3.2。**
