# Research Quality v1 人工标注规范

当前文件包含 **90 条候选**，不是独立人工审核的 Gold Set。所有样本为 `pending_review`、`split=unassigned`，没有人工审核记录。参考答案由 Coding Agent 根据固定 Prepared PDF 原文整理；原文位置校验通过不代表语义标注正确。

## 来源与边界

- 来源限定为 Stage E 冻结语料的 CLIP、BLIP、BLIP-2、Flamingo、LLaVA 五篇固定版本；不使用 Stage E 模型 Claim、Paper Profile 或模型记忆作为参考答案来源。
- 每条正向证据记录 Evidence ID、PDF 页序号、印刷页码（无法识别时 null）、章节/区域说明、原 PDF SHA256、版本 URL、region/block 坐标与原文引句。
- `gold_evidence_ids` 是待审核的来源标签，不意味着候选已经是 Gold。必须核实参考答案的全部细节都被引用原文支持。
- 不得将当前 Prepared Corpus 缺失某信息写成“整篇论文没有该信息”。缺页、私人 manifest、caption-only 等问题要按指定访问范围判断。
- Caption 是文字证据；`provided_caption_only` 不允许用像素或 VLM 回答 RGB 等问题，即使仓库中存在图像资源。

## 样本结构与分组

30 条事实核验覆盖每篇 6 个有来源的问题。Reviewer 30 条为 15 组来源支持的正例和明确标记的受控反例；反例是待审核的错误论断，不是伪造的参考事实。比较 15 条为 BLIP-2/Flamingo/LLaVA 三组两论文组合 × 五个维度。

边界 15 条包含 5 条私人 manifest、5 条未准备页、3 条 caption-only、2 条可回答控制题。拒答标签分布为 **13 应拒答 / 2 应回答**；不能只报总准确率而忽略类别不平衡。全部样本共享 34 个显式事实/边界组；90 条记录不等于 90 个独立事实。

`fact_group_ids` 必须覆盖所有共享或改写的事实，包括事实题、Reviewer 正负例、比较中的逐论文事实和可回答控制题。跨论文比较会连接多个组；同一组不得同时进入 dev/test 或模型优化数据。语义改写是否共享同一事实需要人工检查，校验器不是语义相似度模型。

所有样本均声明 `usage_partition=benchmark_only`，禁止用于训练、Prompt 优化或阈值调节。该标记和校验不能检测仓库外未申报的训练使用；维护者必须遵守隔离约定。

## 审核参考答案

1. 打开 `source_url` 对应固定版本 PDF，在指定 PDF 页与坐标核对引句。核对 Source Manifest 哈希，注意印刷页码与 PDF 页序号的差异。
2. 阅读原始段落的上下文，确认参考答案没有省略改变语义的限制、把 future work 写成现有机制、把实验局部结果写成通用性能或混淆模型版本。
3. 对 Reviewer 负例核对完整待审论断。`passed` 表示全部被所供证据支持；`rejected` 表示矛盾、部分支持或证据不足。一个子句正确不能抵消另一个子句的错误。
4. 对比较逐篇独立核验事实与证据归属，再审核差异点。资料没有给出 Flamingo 的具体 LM 家族时，不能据此断言其家族与 LLaMA 不同。
5. 对边界题核对访问范围。可回答控制题不能无理由拒答；未准备页不能临时解析 PDF；私人 dataset manifest 不能用公开 PDF 哈希替代。

## 状态与审核记录

未审核只能保持 `draft` 或 `pending_review`。审核未完成或需要修改时继续 `pending_review`，记录 `needs_changes`；不得批量假定批准。

正式 `approved_gold` 至少要求：实际人工审核者、带时区时间、`human_original_pdf_review` 方法、approved 结论、完整 checked_evidence_ids、非空审核说明，以及分组一致的 dev/test 划分。边界题还必须 `checked_boundary_scope=true`。这些是结构契约；工具不会独立验证填写者是否真人。

示例（**示意，不能复制成已完成审核**）：

```json
{
  "reviewer": "实际审核者",
  "reviewed_at": "实际带时区的审核时间",
  "method": "human_original_pdf_review",
  "verdict": "approved",
  "checked_evidence_ids": ["逐条核验过的实际 Evidence ID"],
  "checked_boundary_scope": true,
  "notes": "记录原文支持范围、修订和边界核对结果"
}
```

`annotation_template.py` 输出带 null 的待填写表单。模板不能直接作为完成的审批/评分记录载入。人工批准要更新对应 case 的 human_reviews、annotation_status 和 split；没有自动批准功能。

人工编辑 JSONL 后需要维护 manifest 的文件 SHA256/行数，再执行完整来源校验。只更新清单，不能改论文语料以配合答案。可在候选目录副本中维护新版本；准备脚本会拒绝覆盖任何已编辑文件。

```python
import hashlib, json
from pathlib import Path

directory = Path("data/benchmark/research_quality_v1")
path = directory / "manifest.json"
manifest = json.loads(path.read_text())
for entry in manifest["case_files"].values():
    source = directory / entry["filename"]
    entry["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    entry["count"] = len([line for line in source.read_text().splitlines() if line.strip()])
path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
```

## 预测与人工语义评分

模型输入必须用 `evaluation_input(case)` 投影：问题、目标论文、维度，以及 Reviewer 待审论断/给定原文。禁止直接把完整 case（参考答案、预期标签、判定理由、比较差异点）放进 Prompt。

预测 JSONL 每条包含 `case_id/prediction_id/system`。Reviewer 输出结构化 `reviewer_label=passed|rejected`；边界输出 `abstained=true|false`。开放事实/比较输出 `answer`。

事实与比较的正确性由人审 `semantic_correct` 给出。即使字符串完全相同也不会自动获得正确分；字符串不同的正确改写也可被人工判为正确。评分器没有调用生产 Reviewer 或第二个 LLM Judge。

人工评分必须记录 case SHA256 和 prediction SHA256；任何样本、答案或预测身份变化都会使旧评分失效。请先完成 Gold 审核和版本/划分，再创建与该版本匹配的预测评分表。

比较任务另有四个 0/1/2 分项：

| 轴 | 0 | 1 | 2 |
| --- | --- | --- | --- |
| fact_accuracy | 有关键错误或无事实 | 部分正确但有错误/遗漏 | 逐论文事实准确且限制正确 |
| difference_correctness | 差异错误或无比较 | 部分差异有依据 | 差异/共同点准确，不推断未知信息 |
| evidence_grounding | 关键主张无有效来源 | 部分主张来源不足 | 逐篇证据支持所有比较主张 |
| scope_completeness | 偏离论文/维度 | 部分范围覆盖 | 覆盖指定维度并正确保留缺失 |

整体 `semantic_correct` 仍由人工独立给出；评分器不按总分阈值推导正确性。完整四轴必填，避免缺项被当成满分。

## 分母、缺失与解释

- 正式评分只使用 `approved_gold`，默认 test。候选、rejected 或其他 split 不进入分母。
- 缺失预测/任务必需字段缺失是显式 operational failure，计入已知正确性分母的失败；缺少人工评分的开放答案记 pending，不伪造分数。
- accuracy 是 known-correctness 的正确数/已评分数，同时报告 eligible、pending、missing、invalid 与完成率；完成率不足不得包装为完整质量结果。
- Reviewer 正类为 passed。TP/FP/FN/TN 仅统计有效二元标签；错误接受率 FP/(FP+TN)，错误拒绝率 FN/(FN+TP)。缺失预测不被编造为二元标签，但仍在任务总体准确率中作为失败。
- 拒答准确率只比较结构化决策与 Gold 决策，不自动判断拒答解释的语义质量。
- 比较四轴各汇总为实际分/最大可能分，四轴平均为 sum(scores)/(8×已人审比较数)。零分母返回 null。
- 跨论文样本整体计一次，在每个相关论文分组各计一次；按论文统计的分母不能简单相加。

F1 只交付框架与候选；不能宣称 90 条已是人工 Gold，也不自动进入 F2 或运行真实 Provider。
