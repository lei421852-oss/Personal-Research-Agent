# Agent-controlled Multi-Query Paper Discovery

在 Phase 1 上增加模型控制的查询扩展，保持单 Agent、原有限步循环和 PaperRetriever 的单查询契约。不引入 Planner、Multi-Agent、Async、线程池、RRF、BM25、Reranker、Reviewer 或 Checkpoint。

## 职责与硬限制

- `QueryExpander` 只用原始 Research Query 调用一次现有 LLM，返回保留的 `original_query` 与最多两条 `expanded_queries`。它看不到候选列表、目录或 Relevant Labels，也不执行 Retrieval。
- `expand_search_query(query)` 是正式 Tool。必须传原问题；已知论文或高度具体的 Known-item 通常不需要。开放集合问题在原查询覆盖不足时，由 Policy 决定是否调用以及是否执行所有生成的视角。
- Controller 只验证 Schema、原查询绑定、允许的查询集合、重复查询和预算；没有根据 CLIP/BLIP、题号或词表决定是否扩展。
- 每个请求最多一次扩展执行尝试，生成失败也占用这个名额。最多两条扩展；原查询必须保留并先执行，后续仅接受原查询或已经验证的扩展查询，避免绕过生成工具制造 Query Explosion。
- 第二次扩展为 error/expansion_budget_exceeded，未执行，不调用生成模型。重复 Paper Query 也不会再次访问 Embedding/Index。拦截占模型决策机会，不增加实际 Tool Call。
- 保留原 6 次工具执行 / 7 次主策略决策预算；生成最多额外 1 次 LLM 请求。因此总 LLM 请求数与主策略决策数不同，API 指标包含生成请求。

空字符串、只有空白/标点、原查询重复、兄弟查询重复或超过 2 条都会拒绝。允许生成空列表表示无法给出忠实视角，此时 Observation 为 empty，原查询仍可用。

**意图保持和“不新增无依据论文/方法名”属于生成模型的语义契约。** Prompt 明确要求保持原约束、禁止猜答案或引入原问题没有的命名方法；Controller 不假装能用关键词完备证明语义忠实性。本轮实际查询均保存供人工核对，未新增具体论文名或命名方法，不能据此承诺未来模型输出必然正确。

## Candidate Union 与可观察来源

每次 search_papers 的 Observation 包含 query、top_k 与候选。Candidate Papers 按 paper_id 合并，保留首次出现顺序，每篇的 retrieved_by 保存所有召回 Query、该查询中的 rank 和 score。

Domain Trace / API Trace 增加兼容字段 `retrieved_paper_ids`，与 `evidence_ids` 分开。UI 的步骤详情显示 Candidate Papers 数量和 ID，结合 arguments.query 可直接检查“哪条 Query 召回哪篇论文”。候选不是正文证据，不能冒充 Citation。

这是并集与去重，不做分数融合、RRF 或重排。生成结果不会自动检索，只有 Agent 选择的查询才执行。

## 固定评测

沿用 Phase 1 完全相同的 8 个 query、25 篇 Paper Corpus、Qwen/Qwen3-Embedding-0.6B、Chroma Paper Index 与 Relevant Labels。未改 fixture 或 Gold，未重建索引。每条实际执行的查询 Top-K 固定为 10。

```bash
python -m eval.adaptive_paper_discovery --env-file .env \
  --output artifacts/adaptive-paper-discovery.json
```

这是显式真实运行，会调用 LLM；默认单元测试不会运行它。逐题写出结果，不执行 V0.1 QA 或多 Agent 评测。

### 两种口径必须分开

1. **固定总候选预算 Recall@5/10**：取稳定并集中前 K 个候选，与单查询的 Top-K 比较。原查询 Top-10 在最前，追加候选不会伪装成更好的 Top-10 排名。
2. **每条查询 Top-K 并集覆盖率**：对所有实际 Query 的前 K 结果取集合，衡量发现覆盖。候选可能超过 K，必须同时报告候选数量，不能称为融合后的 Top-K。

ExpansionGain 分别报告这两种口径相对 Baseline 的增量及新增相关 ID。ExpansionRate 是实际调用生成器的 case 比例；另外保留成功生成比例。LLM、Embedding、Latency 均实际计数/计时；LLM 中包含主策略与生成器，成本初始化不计入请求耗时。

## 本轮真实结果

| 系统 / 口径 | Recall@5 | Recall@10 |
| --- | ---: | ---: |
| Single-Query Baseline | 0.8708 | 0.8958 |
| Adaptive · 固定总候选 K | 0.8708 | 0.8958 |
| Adaptive · 各查询 Top-K 并集覆盖 | 0.8708 | 0.8958 |

ExpansionRate = 3/8 = 37.5%。模型在 discovery-01/03/08 调用生成器，分别执行 1/1/2 条替代查询；另外 5 题跳过。生成器共输出 6 条合法查询，实际执行 4 条。未为了指标补跑没有被模型选中的查询。

| 成本（8 题合计，均为实际调用） | Baseline | Adaptive |
| --- | ---: | ---: |
| LLM Calls | 0 | 26（主策略 23，生成 3） |
| Query Embedding Calls | 8 | 12 |
| 平均请求 Latency | 118.8 ms | 9155.6 ms |

额外耗时合计 72.294 秒。每题 ExpansionGain 均为 0；候选规模增加，但没有补齐标注中的相关论文。Baseline 与 Phase 1 数值一致。

### 两个重点 Case

- **discovery-03 / InstructBLIP**：原 Top-10 未命中；模型生成 2 条视角，只执行 1 条，新增查询仍未命中。
- **discovery-08 / OFA**：原 Top-10 未命中；模型执行 2 条新查询，仍未命中。

保留所有 query、候选及来源，不通过补关键词、修改标签、增加 K 或引入答案方法名修饰结果。本轮证明扩展受控与来源可观察，**没有证明召回提升或成本收益**。

评测仅为 metadata discovery：finish 保留空 evidence_ids 与明确限制，因此旧 AgentResult 的 stop_reason 为 insufficient_evidence；`metadata_discovery_finished` 表示有效 finish 已执行，不是科学问答通过。没有把候选元数据改造成正文证据。

另做真实 CLIP、BLIP Known-item 回归，两题均直接 search_text → finish，2 次 LLM 调用，无 search_papers / expand_search_query。

## 回归与边界

完整 306 项测试通过（原 284 项保留，新增 22 项）。覆盖预算拒绝、重复/空查询、生成不检索、Source Union、Known-item、原查询保留、API 额外生成调用计数、Trace 的论文/证据 ID 分离与固定 K/并集指标区别。Ruff、127 个 Python 文件格式检查、差异检查通过。

模型仍可能给出质量不高或约束不完备的改写；词元不同不代表语义视角足够不同。Semantic Sufficiency 与 stopping 判断由模型负责，预算只是失败约束。Phase 1 标签仍是开发候选标注，不是正式人工批准的 benchmark。
