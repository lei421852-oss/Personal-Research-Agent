# Stage E：来源可核验的研究语料与跨论文评测

> 状态更新：下面保留语料工程实现阶段、真实评测执行前的历史记录。双论文、三论文和检索回归产物现已完成，并在本轮最终验收中独立复核；当前结论、日志、来源指纹与限制以 [最终冻结验收报告](acceptance/stage_e_freeze/acceptance_report.md) 为准。没有改写历史评测产物。

本轮完成 E1 语料处理、来源校验和 E2 评测入口。**真实 Embedding 检索评测与真实 Worker/Reviewer/Synthesis 评测尚未执行**；状态为 `not_executed`，原因是本轮执行计划要求先交付命令，用户确认后运行。Scripted/Fake 结果只验证工程连线，不作为研究质量、覆盖率提升或 Benchmark 证据。

## Stage D 冻结基线

起始提交 `6df8d8d`，工作区干净；起始全量测试 **703 passed**。`RecoveryConfig.max_recovery_rounds=1`，再次拒绝成为 `review_rejected`，历史保存初次/恢复 Worker 与 Review 快照。恢复通过必须再次审核；取消、执行失败和审核不可用分别保留。已有回归覆盖这些行为，本轮没有发现阻塞性问题。

本轮没有修改 Planner、Worker、Reviewer、Recovery、Synthesis、Profile、LLM/VLM Provider、Retriever、Chroma 实现或 API/UI。唯一修改的既有运行文件是 `app/evidence/corpus.py`：增加可选的额外语料目录，并在加载具有来源清单的数据时验证 provenance；既有调用签名和默认三篇语料保持兼容。

## 语料快照与目录

| 范围 | 论文 | TextEvidence | FigureEvidence |
| --- | --- | ---: | ---: |
| 默认 V0.1 语料 | CLIP、BLIP、Flamingo | 25 | 3 |
| Stage E 新增 | BLIP-2、LLaVA | 21 | 2 |
| 显式加载的研究语料 | 上述五篇 | 46 | 5 |
| 三论文研究目标子集 | BLIP-2、Flamingo、LLaVA | 30 | 3 |

新增论文放在 `data/research_papers/{blip2,llava}`。现有三篇仍位于 `data/papers`。研究入口调用同一 `load_paper_corpus(..., additional_papers_dir=...)`；工具契约和向量排序保持不变。这样不会让 V0.1 冻结 40 题或原“发现论文但没有正文”回归测试改变含义。

两组目录均有 `source_manifest.json` 与 `prepared_evidence.jsonl`。三篇目标来源：

| 论文 | 固定版本 PDF | SHA-256 |
| --- | --- | --- |
| BLIP-2 | [2301.12597v1](https://arxiv.org/pdf/2301.12597v1) | `0a4b93c402a6b8b0d2c5f438bbae31d447b17324b71c3e4fb6dab53e0e389bc5` |
| Flamingo | [2204.14198v1](https://arxiv.org/pdf/2204.14198v1) | `79d131280bc79f659212f35f940dfd3a26013c73355a58eb2263410945780c4c` |
| LLaVA · Visual Instruction Tuning | [2304.08485v1](https://arxiv.org/pdf/2304.08485v1) | `0d5be40caf852d51a0e2636619fafc5b910788af9584fdc392abc652787560ed` |

BLIP-2 准备 PDF 页 1/2/3/4，11 个完整选定段落和页 4 Figure 3；LLaVA 准备页 1/4/5/6，10 个选定段落和页 4 Figure 1。旧 Flamingo 的证据 ID、内容、图像和页码不变。这里只准备固定区域，不是任意 PDF 解析器。

## 抽取与校验边界

复用 `PaperFixtureConfig` 与 `prepare_fixture`。坐标来自原 PDF 的段落布局检查；BLIP-2 双栏按明确区域读取，段落之间不合并，跨页残段不自动拼接。LLaVA 图内有异常 LaTeX 文字层，保留原像素裁剪，排除该文字层和独立公式。表格、未准备段落和公式不能凭模型记忆补齐。

每条记录保留论文 ID、PDF 页序号、章节标题、原文、PDF 哈希、区域/块坐标与稳定 Evidence ID；Figure 另有图号、图注和关联正文 ID。`pixels_available` 表示确有固定裁剪文件，**不表示 VLM 已看过它**。图注或 `read_page` 不能冒充像素分析；Reviewer 仍只接收文字/图注。

校验会对照 PDF 重新抽取原文和图注，重新渲染固定裁剪并核对图片哈希，检查页码映射、来源版本、重复 ID、跨论文关联和投影一致性。新目录缺少完整 provenance 时禁止加载。程序不接收 Paper Profile 作为 Prepared Evidence。

同一来源/配置重复准备保留时间戳并生成相同数据与 ID；新索引使用 upsert。一次进程内按文件路径、大小、修改/变更时间和领域数据摘要缓存成功校验，文件发生变化即重新检查；这不是签名认证或抵御恶意文件系统的安全机制。

## 冻结评测契约

`eval/datasets/stage_e.json` 为 `stage-e-v1`，共有 **22 个案例**：15 个原始 Paper×Dimension 单论文需求、1 个双论文连接机制比较、1 个原三论文五维度研究、以及缺失证据、错误前提、图注/像素、跨论文 ID 污染、审核拒绝/恢复各 1 个。

原始计划、15 个 Requirement ID 和研究问题原样保留。来源标签经过 PDF 区域/存在性检查，状态为 `source_checked_candidates`，**尚不是独立人工批准的穷尽相关性 Gold**。标签、期待行为和答案提示不会进入 Worker；Worker 只接收 pending 需求及其问题。

为了固定维度与 Stage D 比较，默认重放此前真实 Planner 计划，`planner_model_calls=0`，不重新规划。评测链为 **真实 Planner 计划重放 → Worker → Reviewer → 一轮 Recovery → Synthesis**，不能称为本轮又执行了一次真实 Planner。污染/恢复案例是受控契约测试，真实 CLI 拒绝将其当作自然失败评测执行。

## 评测入口与指标

`configs/cross_paper_eval.json` 固定语料快照、独立索引目录/collection、Qwen/Qwen3-Embedding-0.6B、CPU、cosine 和 Top-10。配置化的初次 Worker 最多 20 模型预算/8 工具预算，Reviewer 每需求最多 3 模型调用；恢复保持一轮、每 Worker 12 模型/4 工具预算。该评测专用全研究预算为 **200 模型 / 64 工具**，只覆盖本次单案例的各阶段；没有修改 Stage D 默认 80/32。

LLM 并发 2、VLM 1、检索 1、Worker 3；每依赖超时 60 秒，transient retry 最多 2。Provider 模型名称由运行时环境读取并记入结果；`temperature` 沿用冻结 Adapter，未发送参数，明确记录为 provider default，不能声称固定为 0。SDK 重试仍为 0，依赖重试继续走既有 Adapter。

检索评测以原 15 个来源标注需求为样本，同一 query/范围/标签下比较旧三篇和新增五篇快照，分别计算 Macro Recall@5/@10。无正向标签的证据不足题不进入召回率分母。旧快照不含新增论文证据，因此总体差异同时反映语料可用性，**不能归因为排序算法变好**；结果保留每个 requirement 以便单独看 Flamingo 五题回归。

研究指标包括 supported/review passed/rejected、recovery attempted/converted、verified coverage、比较数量、结构引用有效率、模型/工具调用数和耗时。ResolutionRate、EvidenceCoverage、ReviewPassRate、ReportGenerationSuccessRate 分别记录。不存在比较时引用有效率为 `null`，不能误报 100%。

`unsupported_claim_count` 统计初次/最终快照中 Reviewer 拒绝的不同 Claim，是**审核诊断计数**；引用/身份违规由确定性验证拒绝交付。这不包含独立语义蕴含评估，不能称为真实幻觉率。恢复后的事实与缺失单元再经原 SynthesisInputBuilder/OutputValidator 检查。

模型预算计数沿用 Worker 的保守 VLM tool-attempt 口径，不是精确账单；真实 LLM Provider 尝试数另记，VLM 精确物理次数不能从现有 Provider 直接获得。执行失败保存已完成 Worker/Review 与已知计数；取消继续传播并保留快照。

## 已执行检查

- 来源与领域投影校验：五篇共 51 条记录均通过，两个确定性有效率均为 1.0。
- 原 V0.1 语料、40 题评测、原始 28 条 Evidence 及核心冻结文件保持不变。
- 双论文与三论文 Scripted Smoke 跑通，真实 Provider 调用为 0；只验证报告网格、审核/恢复接入、比较的多来源引用和 Trace。
- 初始 703 项测试保留，新增 53 项来源、隔离、评测与失败/取消测试；全量 **756 passed**，保留一条既有 Starlette TestClient 弃用提示。
- **真实 Recall 数值、真实双论文研究结果、三论文真实比较和相对 Stage D 的真实 Coverage 变化尚未产生。**

## 准备、校验与受控执行

在仓库根目录、激活已有虚拟环境后运行。PDF、人工配置和已准备文件已在本地仓库；不需要在线下载新论文，也不需要画像 LLM。

```bash
python -m scripts.prepare_research_corpus --papers-dir data/research_papers \
  --config data/fixture_configs/blip2.json --pdf data/research_papers/blip2/paper.pdf
python -m scripts.prepare_research_corpus --papers-dir data/research_papers \
  --config data/fixture_configs/llava.json --pdf data/research_papers/llava/paper.pdf
python -m scripts.prepare_research_corpus --papers-dir data/papers
python -m scripts.validate_research_corpus --output "$STAGE_E_OUTPUT_DIR/corpus-validation.json"
python -m scripts.evaluate_cross_paper --output "$STAGE_E_OUTPUT_DIR/preflight.json"
python -m scripts.evaluate_cross_paper --scripted-smoke --case-id two-paper-bridge \
  --output "$STAGE_E_OUTPUT_DIR/two-paper-scripted.json"
python -m scripts.evaluate_cross_paper --scripted-smoke --case-id three-paper-core \
  --output "$STAGE_E_OUTPUT_DIR/three-paper-scripted.json"
python -m pytest -q
```

`STAGE_E_OUTPUT_DIR` 是调用者指定的输出目录；本次用户产物实际放在既有项目的 Codex-Generated-Artifacts 目录。默认入口只预检，绝不会初始化 Embedding 或调用 Provider。

## 等用户确认后执行的真实命令

先运行 15 需求检索回归，再运行较小的双论文连接机制案例，最后另行执行三论文五维度案例；不默认跑全部 22 案例。真实模式要求 `.env` 中已配置 LLM/VLM，文件保持 Git 忽略。

```bash
python -m scripts.evaluate_cross_paper --execute-retrieval \
  --output "$STAGE_E_OUTPUT_DIR/retrieval-before-after.json"
python -m scripts.evaluate_cross_paper --execute --case-id two-paper-bridge \
  --output "$STAGE_E_OUTPUT_DIR/two-paper-real.json"
python -m scripts.evaluate_cross_paper --execute --case-id three-paper-core \
  --output "$STAGE_E_OUTPUT_DIR/three-paper-real.json"
```

检索模式首次建立独立的 Stage E 与 before collections，复用同一个 Embedding 实例，不覆盖 V0.1 collection。后续复用前核对绑定配置和每条索引记录；索引有旧 ID、改写正文或不同快照时拒绝使用。Provider 未配置标记 `not_executed`；已启动后阶段错误标记 `execution_failed` 并保留真实执行快照。

## 待核验与限制

1. 原五维度描述含潜在答案提示警告，原计划已记录；为保持基线，本阶段未修改它。
2. BLIP-2 跨页架构/数据段落未拼接，精确参数数量和完整训练数据可能仍缺失；不能从 Profile 或模型记忆补全。
3. 来源标签需要人工检查是否穷尽相关证据；当前 Recall 只能作为所标注来源的检索诊断。
4. 有三篇语料不保证三篇都通过语义审核；比较仅组织同维度的已核验逐篇主张，不新增胜负或等价判断。
5. Scripted Smoke 不证明回答质量；真实研究质量验收仍待用户确认执行。没有正式 Benchmark、没有新增论文发现/检索算法或研究循环。
