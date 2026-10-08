# Stage F1 · Research Quality Benchmark 工程验收报告

验收日期：2026-10-08。结论：**PASS（F1 工程范围）**。这不代表参考答案已通过独立人工审核，也不代表模型研究质量评测已经完成。

## 1. 基线与范围

- 从本地 `stage-e-freeze-v1` 创建 `feat/stage-f1-research-quality-benchmark` 分支；开始时工作区干净。
- Stage E 标签提交：`5700c6e31d0a46fefa44f890fb7fbb1b60634124`。
- 本轮受测 F1 代码提交：`c5f300c76a6f296fca5280f4a65d7e15f17c0f76`，提交名称 `feat: add independent research quality benchmark framework`。
- F1 只增加独立评测模块、候选数据、脚本、测试和文档。Stage E 原有 297 个跟踪文件均未改动，292 个冻结文件的 SHA256 全部匹配。
- Stage E 聚合指纹仍为 `44c7afd36c793eff86faed077b7c917a5c6a200e3d652bfbf5b19e83cff0362e`。原始冻结清单、报告和标签保留。
- 本轮真实 Provider 调用数：**0**；远程 Git 操作数：**0**；未启动 F2。

## 2. 数据交付与来源

| 类别 | 候选数 | 说明 |
| --- | ---: | --- |
| 单论文事实核验 | 30 | 五篇论文各 6 条，有具体 Prepared PDF 正文来源 |
| Reviewer | 30 | 15 个正例、15 个明确标记的受控矛盾反例 |
| 跨论文比较 | 15 | BLIP-2、Flamingo、LLaVA 的三组两论文组合 × 五个维度 |
| 证据不足与边界 | 15 | 13 个应拒答样本、2 个可回答控制样本 |
| 合计 | **90** | 数量短缺为 0；全部 pending_review、split=unassigned |
| 实际人工已审核 | **0** | 未登记或假定任何真实人审 |
| 正式 approved_gold | **0** | 当前正式评分分母为空 |

数据目录：`data/benchmark/research_quality_v1/`。其中包含 manifest、四份 JSONL 及中文标注指南。

参考答案由 Coding Agent 根据固定版本原 PDF 的 Prepared Text/Caption 整理，共关联 **32 个原始 Evidence ID**。正向来源记录论文 ID、PDF 页序号、印刷页码、章节/区域、原文引句、原始 PDF SHA256、版本 URL 与 source_span 坐标。没有读取 Stage E 生成的 Claim 作为标准答案，没有用 Paper Profile 或摘要推测缺失技术细节。

来源论文为 CLIP `2103.00020v1`、BLIP `2201.12086v2`、BLIP-2 `2301.12597v1`、Flamingo `2204.14198v1`、LLaVA `2304.08485v1`。语料快照 ID：`18fce87eb4050186aefc545a97b55d8b397108953647c5985c685102b2ea2390`。没有增加论文、页或图。

私人 manifest 与未准备页等题没有正向 Gold 证据，使用明确的 prepared-corpus 访问范围、快照和拒答理由。语料中缺少某信息不能改写为整篇论文或现实世界不存在该信息。Caption-only 题只供图注，不能据图像像素回答精确 RGB。

## 3. 数据模式与隔离机制

`eval/research_quality/schemas.py` 定义公共 Case 契约，以及 ReviewerTarget、ComparisonTarget、AbstentionTarget、SourceLocation、HumanGoldReview、QualityPrediction、HumanAssessment 和比较四轴评分。

每条 Case 保留用户要求的 `case_id/task_type/paper_ids/question/reference_answer/gold_evidence_ids/source_locations/expected_behavior/annotation_status/annotation_provenance`，另外记录研究维度、事实分组、split 和用途隔离标记。

正式 Gold 必须具备实际人工审核者、带时区时间、原 PDF 审核方法、approved 结论、完整核验 ID 与说明，并已划入 dev/test；边界题额外要求访问范围核验。Pydantic 与 Gold 选择器会重新验证输入，不能只通过修改 status 或 model_copy 绕过检查。工具不能认证审核者身份，维护者必须如实记录实际审核。

全部候选声明 `benchmark_only`，manifest 禁止 training、prompt_optimization、threshold_tuning。90 条记录共享 **34 个事实/边界组**。校验器阻止显式共享组跨 dev/test 或评测/优化用途，并发现完全相同的规范化参考答案未共享组的情况。跨论文比较连接多个事实组，后续划分必须处理这些关联，不能随机逐行拆分。

`evaluation_input(case)` 仅投影问题、论文、维度及任务必需的待审论断/给定原文，不把参考答案、预期标签、判定理由或比较 Gold 差异传给未来被测模型。

## 4. 校验与评分

- `dataset_loader.py` 检查 manifest 中 JSONL 哈希与行数，拒绝跨目录路径，加载后按 Schema 验证。
- `dataset_validator.py` 核对论文登记、证据归属、页码/版本/哈希/坐标/引句、比较双方来源、边界范围、状态与分组隔离。它不做自然语言语义蕴含判定。
- `scoring.py` 不依赖生产 Reviewer。Reviewer 用 `passed/rejected` 二元标签；拒答用结构化 `abstained` 决策。
- 开放事实与比较必须读取人工语义评分，并绑定 Case 与 Prediction 的 canonical SHA256。参考答案完全相同仍不能自动获得正确分；答案或样本修改后旧评分会失效。
- 比较的 fact_accuracy、difference_correctness、evidence_grounding、scope_completeness 四轴各 0..2。整体正确性另由人工给出，不按总分阈值推导。
- `metrics.py` 汇总总体及按论文、维度、任务的分组结果，保留每项 numerator/denominator/value。零分母 value 为 null。

缺失/必需字段无效的预测记为明确 operational failure；缺少人工评分的开放答案记 pending，不伪造正确或错误。准确率分母使用已有正确性判定的条目，并同时报告 eligible、pending、missing、invalid 和完成率。

Reviewer 混淆矩阵正类为 passed：错误接受率 FP/(FP+TN)、错误拒绝率 FN/(FN+TP)。这些二元分母只使用有效标签；缺失预测仍计入任务整体的失败数，但不被伪装成 passed/rejected。跨论文 Case 整体计一次、各相关论文分组各计一次，论文分母不可相加。

本轮无 Gold 评分演示状态为 `not_scored_no_approved_gold`，准确率为 `0/0 → null`。这证明入口和排除机制正常，不是模型准确率为 0，也不是模型质量通过。

## 5. 本轮实际执行结果

| 检查 | 结果 | 退出码 | 日志 |
| --- | --- | ---: | --- |
| F1 相关测试 | **73 passed** | 0 | `related_tests.txt` |
| 全量工程回归 | **829 passed，1 个既有警告** | 0 | `full_tests.txt` |
| Ruff | All checks passed | 0 | `ruff_results.txt` |
| Formatter | 201 files already formatted | 0 | `formatter_results.txt` |
| Git 空白检查 | 通过 | 0 | `whitespace_results.txt` |
| 90 条数据来源/结构检查 | structurally_valid | 0 | `dataset_validation.json` |
| 无 Gold 评分入口 | 空分母、null，不启动 Provider | 0 | `no_gold_scoring.json` |
| 重复 preparation | 数据与 manifest 字节完全一致 | 0 | `cli_checks.json` |
| Stage E 冻结核验 | 292/292 SHA 匹配、297 原文件未改动 | 0 | `freeze_verification.json` |

实际命令见 `test_checks.json/static_checks.json/cli_checks.json`，包含开始时间、耗时与退出码。核心测试命令为 `python -m pytest -q tests/unit/research_quality` 与 `python -m pytest -q`；Ruff 沿用当前环境已有可执行文件，未增加依赖或改检查规则。

测试覆盖有效加载、非法论文/证据、重复 Case、假 Gold、错误引用归属、分组/用途泄漏、零分母、缺失预测、有效标签混淆矩阵边界、开放题人审与哈希绑定、比较 rubric、重复评分一致性及输入不被修改。测试中的人审/预测记录全部是明确命名的 synthetic unit fixture，不写回候选数据，也不作为正式 Gold 或真实性能证据。

Stage E 原测试 756 项保持通过，F1 增加 73 项，合计 829。既有警告来自 Starlette TestClient/httpx 弃用提示；不阻塞 F1，本轮没有升级依赖。

## 6. 已知限制与停止点

1. 人审与正式 Gold 均为 0。需要实际逐条原文审核后才可做正式研究质量评分；F1 工程 PASS 不能替代这一步。
2. 90 条样本是相关候选，不是 90 个独立事实。34 个共享组、比较关联及 13:2 拒答分布必须随结果报告。
3. 结构性来源校验不能证明参考答案全部被原文语义支持，也不能认证人工身份或检查仓库外未申报的训练使用。
4. 显式分组与相同参考答案检查不能识别所有语义改写；后续分组和数据划分需要人工维护。
5. 拒答指标仅验证结构化决策，不自动评判解释措辞。比较质量由人工四轴汇总，不伪装为自动语义正确率。

本轮仅完成 F1。已保留 Stage E 冻结证据；没有执行真实 Provider 评测、远程 Git 或 F2。

代码提交与本报告归档提交分开；本报告引用的 c5f300c 是实际受测实现，后续本地验收提交仅增加本目录的证据文件。最终提交号可通过当前分支 Git 日志核对。
