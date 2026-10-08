# Stage F1：独立研究质量 Benchmark 框架

本阶段基于本地标签 `stage-e-freeze-v1`，仅新增评测数据、验证/评分模块、测试及文档。Planner、Worker、Reviewer、Recovery、Synthesis、Corpus、已有 Frozen Evaluation 与 Stage E 验收证据保持不变。

## 当前状态

| 项目 | 数量 |
| --- | ---: |
| 候选 | 90 |
| 单论文事实 | 30（五篇各 6） |
| Reviewer | 30（15 正例＋15 受控反例） |
| 两论文比较 | 15（三组论文组合×五个维度） |
| 证据不足/边界 | 15（13 拒答＋2 可回答控制） |
| 事实/边界分组 | 34 |
| 实际人工已审核 | 0 |
| 正式 approved_gold | 0 |

参考答案从原始 Prepared PDF 文字/图注整理，不复制 Stage E Claim。全部状态为 pending_review、split=unassigned。校验器证明位置/引用/版本一致，不能代替原文语义核验；90 条相关样本不等于 90 个独立事实。

## 文件与职责

`eval/research_quality/schemas.py` 定义 Case、SourceLocation、HumanGoldReview、Prediction 与绑定哈希的 HumanAssessment。`dataset_loader.py` 在解析前校验 manifest 文件哈希；`approved_gold` 仅返回具备完整人审记录的正式条目。

`dataset_validator.py` 复用现有来源校验，核对论文、证据归属、页码/坐标/引句、比较各方证据、caption-only 范围、标注终态及分组泄漏。它只做确定性结构检查，不运行语义模型。`evaluation_input` 保证未来执行输入不包含参考答案或预期标签。

`scoring.py` 独立于生产 Reviewer。Reviewer/拒答使用结构化标签；开放事实与比较必须读取人工语义评分。`metrics.py` 报告准确率分母、混淆矩阵/错误率、拒答决策准确率、比较四轴、缺失情况和按论文/维度/任务的分组统计。

`annotation_template.py` 输出待填写表单，不能自动登记人审或批准 Gold。完整标注规范见 [annotation_guidelines.md](../data/benchmark/research_quality_v1/annotation_guidelines.md)。

## 离线准备与验证

在仓库根目录、已有虚拟环境运行。先将 `F1_OUTPUT_DIR` 环境变量设置为实际的产物目录；本对话沿用 `D:\Codex-Generated-Artifacts\2026-10-05-多模态论文研究智能体`。以下命令没有任何真实 Provider 执行入口。

```bash
python -m scripts.prepare_quality_benchmark
python -m scripts.validate_quality_benchmark \
  --output "$F1_OUTPUT_DIR/validation.json" \
  --annotation-template "$F1_OUTPUT_DIR/annotation-template.json"
python -m scripts.score_quality_benchmark --output "$F1_OUTPUT_DIR/no-gold-score.json"
python -m pytest -q tests/unit/research_quality
python -m pytest -q
```

准备脚本只读取冻结原文，保留初次 created_at，重复执行输出不变；只要发现目标文件有任何人工编辑即拒绝覆盖。候选目录已经有数据，通常只需运行 validator。

当前评分返回 `not_scored_no_approved_gold`，分母 0、准确率 null，这是正确的未具备正式 Gold 状态；CLI 成功退出只证明框架执行，不是研究质量通过。

## 人工审核之后的评分接口

先根据标注规范完成实际人审、Gold 状态、分组划分与文件哈希维护。预测和人审记录分别使用 JSONL：

```bash
python -m scripts.score_quality_benchmark \
  --predictions predictions.jsonl --assessments human-assessments.jsonl \
  --split test --output "$F1_OUTPUT_DIR/quality-score.json"
```

`QualityPrediction` 使用 `case_id/prediction_id/system`，按任务提供 answer、reviewer_label 或 abstained。HumanAssessment 绑定 case/prediction 的 canonical SHA256；开放题缺少人审只记录 pending，绝不按关键词、字符串完全匹配或生产 Reviewer 的 verdict 推测正确性。

比较分项为 fact_accuracy、difference_correctness、evidence_grounding、scope_completeness，每项 0..2，不能把分项平均自动转换为 overall_correct。计分结果没有“Agent 优于 RAG”之类性能声明。

## 已知边界

1. 当前没有人工 Gold，因此还不能给出真实质量准确率、Reviewer 错误率或人工比较质量。
2. 引句/归属校验不判断自由文本蕴含；人工审批身份由维护者真实填写，工具没有身份认证。
3. 显式 group 与完全相同的规范化参考答案可以检测泄漏；其他语义改写需要人工检查。
4. 比较会连接多个事实组，后续不能随机逐行拆分；小语料产生的关联样本会限制独立样本量。
5. 缺失预测作为失败、开放答案缺少人审作为未评分，报告必须携带完整分母与完成率。
6. 原文双栏/跨页抽取限制沿用 Stage E；标注者应打开对应 PDF 上下文核验，不能从 Profile 或模型记忆补细节。

F1 完成后停止。F2、模型运行、优化或扩大 Benchmark 都需要新的任务授权。
