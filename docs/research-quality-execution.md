# 研究质量评测执行与评分

本入口消费90条AI原文审核参考，复用冻结研究链生成真实预测。已有完整结果在`artifacts/quality-end-to-end/`；完成报告在`docs/acceptance/quality_end_to_end/report.md`。

## 运行

```bash
python -m scripts.run_quality_benchmark \
  --execute --pilot --split dev --output-dir artifacts/quality-end-to-end
python -m scripts.run_quality_benchmark \
  --execute --split all --output-dir artifacts/quality-end-to-end
```

`--execute`才允许真实Provider调用，不带该参数只验证数据。每题完成后保存JSONL；同一输出目录中已执行题不再请求Provider，也不会自动重试失败案例来替换第一轮成绩。预测文件可从执行记录重建；数据哈希或模型改变会拒绝混用已有结果。需要新一轮实验时使用新输出目录。

已知论文/维度固定映射计划，不调用Planner；Gold答案、标签和来源标注不进入研究链输入。Reviewer任务只接收题目规定的待审论断及给定证据。caption-only只给图注，禁用像素/检索。生产Prompt、Retriever和Corpus保持不变。

## 评分

本轮已完成AI核对，实际文件存在，可以直接重算：

```bash
python -m scripts.score_quality_benchmark \
  --dataset data/benchmark/research_quality_v1_ai_reference \
  --reference-kind ai_reviewed --split test \
  --predictions artifacts/quality-end-to-end/predictions.jsonl \
  --ai-assessments artifacts/quality-end-to-end/ai-assessments.jsonl \
  --output artifacts/quality-end-to-end/quality-score-test.json
```

人工实际评分使用`--assessments`和`human_semantic_review`；AI辅助评分使用`--ai-assessments`和`ai_semantic_review`，只允许AI参考类型。二者不冒充、不对同一案例任意选择；都绑定具体Case/Prediction哈希。

运行脚本不自动编写语义评分。新一轮真实输出必须重新核对，不能沿用本轮哈希绑定记录。没有评分的开放答案保持未评分；缺失/无效预测记录执行失败。评分过程本身Provider调用为0，真实执行调用数查看evaluation_summary与executions。

本轮真实/受控区分、分母、失败与AI评分限制均在完成报告中；不要从单元Fake结果或生产Reviewer通过率推导研究质量。
