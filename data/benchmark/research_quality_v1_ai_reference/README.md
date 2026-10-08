# 可直接使用的AI原文审核参考集

90条样本全部为`approved_ai_reference`，`reference_kind=ai_reviewed`。每条包含已完成的AI原PDF复核证明、中文理由、源样本哈希、审核记录哈希以及内容/划分绑定哈希。待审核样本数为0。

审核来源是Codex AI原文复核；本集不宣称是独立人工Gold。`human_reviews`未代填。原候选与人工Gold协议保留在`research_quality_v1`，三处表述修订及初始AI记录保留在`research_quality_v1_ai_reviewed`和`docs/acceptance/stage_f1_sample_review`。

## 划分

| 任务 | dev | test | 合计 |
| --- | ---: | ---: | ---: |
| 事实核验 | 5 | 25 | 30 |
| Reviewer | 6 | 24 | 30 |
| 比较 | 3 | 12 | 15 |
| 边界 | 4 | 11 | 15 |
| 合计 | 18 | 72 | 90 |

划分以共享事实组的连通组件为单位，不使用模型成绩或检索结果选样本。34个显式组不会跨dev/test。由于边界问题按类型共享组，caption-only三个样本统一在dev，private-manifest/unprepared-page统一在test；这是关联隔离的结果，不是逐类型均匀抽样。两侧各有一个可回答边界控制。

## 使用

仓库根目录、已有Python环境下，先设置输出目录。本对话WSL环境可使用：

```bash
export REFERENCE_OUTPUT_DIR="/mnt/d/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体"
mkdir -p "$REFERENCE_OUTPUT_DIR"
```

尚未运行真实评测时，执行下面的来源检查和评分入口检查。省略预测/评分文件参数；评分状态为`awaiting_predictions`：

```bash
python -m scripts.validate_quality_benchmark \
  --dataset data/benchmark/research_quality_v1_ai_reference \
  --output "$REFERENCE_OUTPUT_DIR/reference-validation.json"

python -m scripts.score_quality_benchmark \
  --dataset data/benchmark/research_quality_v1_ai_reference \
  --reference-kind ai_reviewed --split test \
  --output "$REFERENCE_OUTPUT_DIR/2026-10-08-多模态论文研究智能体-评分入口检查.json"
```

实际作答完成后才使用下面的评分命令。`predictions.jsonl`必须是已存在的实际系统输出，`human-assessments.jsonl`必须是对这些输出的实际语义评分；F1不会自动创建它们。请将示例文件名替换为真实文件位置：

```bash
python -m scripts.score_quality_benchmark \
  --dataset data/benchmark/research_quality_v1_ai_reference \
  --reference-kind ai_reviewed --split test \
  --predictions predictions.jsonl --assessments human-assessments.jsonl \
  --output "$REFERENCE_OUTPUT_DIR/quality-score.json"
```

若只有预测而尚无人工语义评分，可先省略`--assessments`：Reviewer/拒答标签可计分，开放答案将保持`pending_human_assessment`。不要用空文件或参考样本的审核记录代替真实预测/语义评分。

加载API：`load_dataset(OUTPUT_DATASET)`；选择API：`approved_references(dataset, split="test")`。评分器默认从该manifest取得AI参考类型，并在结果中记录`provisional_ai_reviewed_reference`。`approved_gold`不会返回这些条目，人工Gold计数不会被AI参考覆盖。

Reviewer和拒答决策可按这些来源已核对的结构化标签计分。开放事实/比较预测仍须绑定到实际预测的单独语义评分；批准参考答案不等于已经审核未来模型答案。没有预测时入口状态为`awaiting_predictions`，不能把缺失预测的计数当成真实模型性能。

本次只完成样本审核、数据划分及可用入口，没有执行F2或真实Provider评测。数据用途仍限定为benchmark_only，禁止用于训练、Prompt优化或阈值调节。

## 重现与编辑

`python -m scripts.finalize_quality_references`从已保存的逐条审核结果确定性构建本目录。缺少、重复、过期或未核验的审核记录会被拒绝；存在不同内容的目标文件会被拒绝覆盖。脚本不会调用LLM或临时生成审核结果。

改变问题、答案、标签、引用、事实组或划分后必须重新完成复核并更新内容绑定哈希；不能只改状态。任何修改还需维护manifest中的文件哈希。
