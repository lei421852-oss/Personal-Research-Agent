# Stage E 最终冻结验收报告

**结论：PASS。建议正式冻结 Stage E。** 验收时间：2026-10-08T08:52:37.522998+00:00。未发现未解决的阻塞项；本轮不是功能开发，也没有重新运行真实模型。

## 基线与执行范围

起始分支 `feat/Structured-tool-calling`，起始 HEAD `9ad7c5c7c2c8a6ffd57c864271b31046ef582f0e`，工作区干净。本轮只新增验收目录并更新 README/Stage E 文档的执行状态；生产代码、测试、Corpus、Gold、阈值、模型配置与研究策略未修改。

证据归档提交 `7c36b7700e7e507d42876098288d16cbb5095cd3`；该提交已包含原始产物的逐字节副本、历史指纹和本轮日志。冻结文件清单以此提交为快照，最终验收提交仅添加证书/报告。`stage-e-freeze-v1` 指向最终验收提交。初次工程测试的代码与冻结代码字节一致。

## 本轮实际回归

- 完整 pytest：**756 passed / 0 failed / 0 errors / 0 skipped**，退出码 0。一条既有 Starlette TestClient 弃用提示；控制台耗时 11.57 秒，精确命令/进程时间见 test_execution.json。
- Ruff check：退出码 0；Formatter：185 文件符合格式，退出码 0；git diff --check：退出码 0。原始输出在 lint_results.txt。
- 原有源码、数据和测试没有修改，因此文档归档前后无需通过改变测试或阈值获得 PASS。
- 独立只读证据审计：**368 项全部通过**，退出码 0，日志 evidence_audit_results.txt。没有调用 LLM、VLM 或 Embedding。

实际命令：

```bash
.venv/bin/python -m pytest -q --junitxml=docs/acceptance/stage_e_freeze/test_results.xml
```

Ruff 使用环境中已有的 `/home/lei/project/projects/personal-rag/.venv/bin/ruff`（0.16.5），检查 `app tests scripts eval`；未安装新检查工具。验收核验器另通过同一 Ruff 的 check/format 检查。完整 JUnit、模块覆盖和特殊预算回归在 test_results.xml / test_coverage.json。

## 模块验收

| 模块 | 结论 | 本轮相关通过用例 | 证明范围 |
| --- | --- | ---: | --- |
| Planner | PASS | 44 | Valid target IDs, dynamic dimensions and exact requirement expansion; Stage E replays the original real plan, zero new Planner calls. |
| Worker | PASS | 135 | Resource/worker semaphore caps, cancellation, retry, timeouts, model/tool budgets and single-action recovery. |
| Reviewer | PASS | 67 | Exact claim/citation binding, semantic pass/reject and provider-unavailable separation; review correctness is not independently measured accuracy. |
| Synthesis | PASS | 64 | Rebuilt verified inputs, rejected/unavailable isolation, comparison whitelist, original authoritative text and missing states; deterministic degraded report revalidated. |
| Evaluation | PASS | 93 | 368 read-only artifact checks, independently recalculated coverage, costs, Recall and source/claim identities; real and scripted evidence separated. |
| Recovery | PASS | 57 | 一轮限制、定向任务、预算跳过、重新审核、原通过不可变、历史保留、取消与失败隔离。 |

模块计数是指定测试文件的本轮 JUnit 汇总；不是新的评测样本量。Planner 核验合法目标范围与需求生成，Stage E 重放已真实生成的原计划，不能声称本轮又执行真实 Planner。

## Stage D 专项证据

| 案例 | 类型 | 恢复轮数 | 恢复结果 | 历史记录 |
| --- | --- | ---: | --- | ---: |
| A | 受控初始拒绝，真实 Worker/Reviewer/Synthesis | 1 | 1 个目标恢复通过；新增模型调用 3 | 20 |
| B | 受控 Fake 再次拒绝 | 1 | 目标保持 review_rejected，原拒绝正文不进入报告 | 20 |
| C | 已有真实输入＋Scripted 综合，Recovery 跳过 | 0 | 0 个恢复 Worker / Reviewer；1 次 Scripted Synthesis | 18 |

逐条核对 history record_id/result_ref、时间戳、状态和原始快照。原 passed Claim/Review 未被恢复覆盖；恢复的新 Claim 必须再次 passed。预算耗尽由本轮 `test_low_global_budget_does_not_start_worker_or_review`、`test_optional_synthesis_budget_is_reserved_before_recovery_workers`、`test_all_insufficient_with_exhausted_prior_budget_needs_no_provider` 等验证。所有相关用例 PASS；B/C 不包装成真实恢复成功率。

## Stage E 真实结果核验

| 指标 | 双论文 | 三论文 |
| --- | ---: | ---: |
| 需求 | 2 | 15 |
| Reviewer 通过且来源校验通过 | 2 | 14 |
| Verified Coverage | 100% | 93.33% |
| 合法跨论文比较 | 1 | 4 |
| 最终结构引用违规 | 0 | 0 |
| 模型调用 | 9 | 41 |
| 工具调用 | 2 | 11 |
| VLM tool attempts | 0 | 0 |
| 最终状态 | completed | completed |

三论文参考值 15 / 14 / 93.33% / 4 / 0 / completed 全部一致。覆盖率从实际 passed Review 精确绑定的需求重算；SynthesisInputBuilder 使用现有可信 Corpus 重新构建输入，与落盘输入相同。每个 Claim 的论文、维度、正文、引用与 Reviewer/Worker 完全匹配；比较至少有两篇对应维度的合法 Claim。

真实标记与本地调用记录一致：双论文 6 次 Worker LLM＋2 次 Review＋1 次 Synthesis＝9；三论文 26＋14＋1＝41，与 llm_provider_attempts_observed 一致。两次均没有 VLM 调用，所以本次不会混淆 VLM attempt 与实际 LLM 计数。真实 SDK 原始请求/隐藏推理未纳入产物；这是本地执行证据核验，不是 Provider 账单鉴证。

缺失单元仍为 Flamingo 的 downstream_capabilities，requirement_id=`req_33be67ee76e452d5de235b6a8e528afa`，status=insufficient_evidence。没有把它由模型知识升级为 verified。ReportGenerationSuccessRate=1、ResolutionRate=1 与 EvidenceCoverage=14/15 分别保留。

## 检索与指标口径

原 15 个需求/同样 query、scope、标签、Embedding、Top-10：

| 语料快照 | 样本量 | Macro Recall@5 | Macro Recall@10 |
| --- | ---: | ---: | ---: |
| Stage D 三篇 | 15 | 24.00% | 33.33% |
| Stage E 五篇 | 15 | 74.00% | 96.67% |

逐例重新计算 Top-K 交集/相关标签数量，再计算宏平均；检索 ID 属于对应论文。旧语料缺少 BLIP-2/LLaVA，指标差异同时反映证据可用性，不能归因为检索算法提升。标签仍为 source_checked_candidates，不是独立人工批准的穷尽 Gold。

结构性引用有效率 1.0 只说明身份、范围、维度与引用并集合法，不能当作语义正确率。unsupported_claim_count 是 Reviewer 拒绝的不同 Claim 诊断计数，未称为真实幻觉率。Scripted 结果 quality_result_usable=false、real_provider_used=false，未用于 Coverage 性能比较。

## 非阻塞问题与后续范围

1. P2：三论文保留 1 项证据缺口；本轮不扩 Corpus 或放宽 Reviewer。
2. P2：真实轨迹存在 6 次有界多动作纠正与若干非法参数反馈；最终恢复并 completed，策略/协议效率优化留到后续。
3. P3：历史检索报告 `reason` 残留预检原因，`status=retrieval_executed`、实际行与调用记录明确已执行。原始产物完整保留，验收分类按已核验的实际结果；不改写历史文件。
4. 未开展独立人工语义准确率、正式 Benchmark 或模型参数可复现性评估，不宣称工程 PASS 意味着所有自然语言事实绝对正确。

没有阻塞级正确性违规、未经 Reviewer 的 Claim 进入报告或冻结文件异常变化。本轮无需业务修复或新增回归测试。

## 指纹、环境与复核

冻结文件：**292 个**，完整 SHA256/大小清单在 freeze_manifest.json，聚合摘要 `44c7afd36c793eff86faed077b7c917a5c6a200e3d652bfbf5b19e83cff0362e`。包括源码、测试、配置、语料及证据归档提交中的日志/原始报告；排除之后生成的自引用验收证书，避免循环哈希。

历史指纹快照五份均已归档。Worker/Stage AB 保护文件未变；Stage C 快照仅有已授权的 Stage D worker、Stage E corpus 变更；Stage D/E 快照仅有已授权 corpus 变更。详情 historical_fingerprint_audit.json。

Python 3.12.3、pytest 8.4.2、Pydantic 2.13.5、Chroma 1.5.9、PyMuPDF 1.28.2、torch 2.13.0+cu130、transformers 4.57.6、OpenAI SDK 2.54.0。完整环境、实际解释器与检查工具路径在 baseline.json / freeze_manifest.json。

只读复核原始证据：

```bash
.venv/bin/python -m docs.acceptance.stage_e_freeze.audit_evidence
```

该命令不调用模型、不修改原始报告，生成的 evidence_audit.json 为确定性核验结果。来源副本 SHA256 在 artifact_manifest.json，原 Stage A～E 验收报告路径在 baseline.json。

本地证据归档与最终验收提交之外未进行远程 Git 操作。最终提交、标签目标和提交后干净状态在外部交付回执中记录，避免证书提交 SHA 的自引用。
