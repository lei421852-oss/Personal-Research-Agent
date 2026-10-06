# Day 8 封板：比较评测接口

当前验收范围仅为冻结数据集可加载、统一 Runner/结果契约，以及 **1 条 dev case × 3 systems 的 Fake/Scripted Smoke Test**。本次收尾不调用真实模型，不执行 10×3 或 30×3 评测，不调整 Agent、工具、检索或标注数据。

## 统一接口

`schemas.py` 的 `EvaluationRun` 已保留：

```text
case_id / system / status / answer / citations / retrieved_evidence_ids
latency_ms / model_calls / vlm_calls / tool_calls
```

三个 Runner 采用同一个接口：

```python
run(case: EvaluationCase) -> EvaluationRun
```

- `TextRAGRunner`：复用 ToolExecutor 文本检索、现有 LLM Client 和 finish 引用验证。
- `FixedMultimodalRAGRunner`：明确 Figure 问题复用 Day 4 MultimodalQAService；其他问题复用文本 Runner。目标来自用户问题和公开语料目录，不读取答案标注。
- `MultimodalAgentRunner`：复用原 BoundedToolAgent，保留原策略及 6 次工具预算。

`base.py` 提供依赖与结果映射。数据集标注只用于离线评估，不进入生成提示、不自动补论文过滤。现有结果中的扩展指标字段可保留 null，不要求本次完成语义评分。

## 唯一推荐的封板验证入口

在项目根目录运行：

```bash
.venv/bin/python -m eval.validate_dataset --require-reviewed
.venv/bin/python -m eval.smoke
.venv/bin/python -m pytest -q
```

`eval.smoke` 固定加载 approved 的 `dev-text-01`，仅构造 fake dependencies，不读取真实模型配置。三个结果均通过 EvaluationRun JSON 序列化/反序列化校验。这个文本案例中 Fixed Multimodal Runner 使用文本分支；此冒烟证明统一接口，不宣称多模态质量通过。原有测试另覆盖 Figure 流程。

测试中的 model_calls / vlm_calls 是模拟客户端计数，不表示外部 API 请求。Fake 答案不用于性能或质量报告。

## 完整实验原设计（未来可选）

冻结集为 10 dev + 30 test，共 40 题；三系统正式 test 对照原设计为 30×3=90 runs。dev 用于开发检查，test 用于正式报告，不反复用 test 调策略。

未来若需要正式实验，继续沿用 run(case)，逐条保存结果并补全以下测量/评分：

| 指标 | 正式实验要求 |
| --- | --- |
| Retrieval Recall@K | 使用明确的真实检索后端，固定/记录 K，区分文本与 Figure 检索及语料覆盖边界 |
| Citation Correctness | 核对引用是否语义支持答案；Evidence ID 合法或命中标注 ID 不能替代语义正确性 |
| Task Success | 按批准的 answer_points、expected_behavior、must_not_claim 人工评分；completed 不等于成功 |
| Execution Cost | 记录总延迟、模型/VLM/工具调用次数；若未来采集 usage，再报告 token/金额；VLM 嵌在工具延迟中时避免重复计时 |

`metrics/`、`run_evaluation.py`、`summarize_results.py` 是此前已建立的可选扩展，继续保留以避免未来重构；它们不属于本次封板必须执行的范围。人工评分未完成时不发布三系统质量排名。

## 历史记录与本次范围

此前按用户“先跑 dev”的选择，已运行并保存过 30 次真实 dev 记录；本次缩小范围后不重跑、不删除、不将其写成本次 Smoke Test 的结果。真实记录当时使用真实 DeepSeek 模型与 day5-controlled 检索，不能视为真实 Embedding 检索实验。test 尚未执行。
