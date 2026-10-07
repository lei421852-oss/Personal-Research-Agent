# 离线论文画像与增强表示

在不修改 PaperRetriever 核心逻辑的前提下，为 PaperDocument 增加 tasks、methods、capabilities。原 fixture 保持不变，旧数据缺少这些字段时默认为空列表。

```text
原始标题和摘要 → PaperProfileExtractor → Enriched PaperDocument
→ retrieval_text → 既有 Embedding → 独立 Paper Index
```

## 抽取与失败边界

- 抽取输入只有 title、abstract，不包含查询、Gold、keywords、其他论文信息或全文。
- 复用现有 LLM Client，要求一个 submit_paper_profile 结构化动作，禁止将自由文本直接写入文档。
- 每条包含 label 与 source_quote。程序校验 source_quote 确实出现在标题或摘要中，再保留 label。
- 每字段最多 5 个短语，按大小写和空白规范化去重；不使用同义词模型或关键词补丁。
- 缺乏依据时提交空数组；非法输出、找不到来源片段或模型调用失败时，当前论文回退为空画像，其他论文继续处理。
- 来源片段存在性校验不等于完整的语义蕴含证明。模型仍可能不准确地概括片段，本阶段没有引入 Reviewer。

PaperDocument.retrieval_text 集中生成以下字段；VectorStore 不自行拼接，PaperRetriever 仍只做 query → embedding → store → TopK。

```text
Title: ...
Abstract: ...
Tasks: ...
Methods: ...
Capabilities: ...
Keywords: ...
```

画像按准备阶段处理，不是 Agent Tool。search_papers 不调用抽取模型，Tool Observation 字段保持不变。旧索引可只读打开；重新摄取一律生成带字段标签的文本，记录 representation_version=paper-profile-v1。

## 准备命令与缓存

在仓库根目录配置 .env 的 LLM_API_KEY、LLM_MODEL、LLM_BASE_URL，然后运行：

```bash
python -m scripts.ingest_papers --enrich --env-file .env --device cpu \
  --profile-cache artifacts/paper-profiles.json --report artifacts/paper-enrichment.json
python -m eval.paper_discovery --output artifacts/paper-discovery-enriched.json
```

--enrich 才会调用画像模型。不带该参数时，仍可直接摄取已有 metadata，但会使用文档自带画像或空画像；保留已抽取结果应使用 --enrich 和同一个缓存。

已包含非空有效画像的 PaperDocument 直接复用；原始文档通过标题和摘要 SHA-256、抽取版本匹配缓存。合法空结果也可复用。失败结果不作为成功缓存，不会在同一轮自动重试。标题或摘要变化、缓存损坏或版本变化时重新抽取。缓存及 Chroma 数据目录均由 Git 忽略，不包含 API key。

## 2026-10-07 真实前后评测

保持 25 篇论文、同一原始 fixture、8 条原查询和 Relevant Labels、Qwen/Qwen3-Embedding-0.6B、每题 Top-10。画像模型没有读评测查询或标签；没有重新生成或修改 Query Expansion Policy。

| Case | Before R@5 | After R@5 | Before R@10 | After R@10 |
| --- | ---: | ---: | ---: | ---: |
| discovery-01 | 100% | 100% | 100% | 100% |
| discovery-02 | 100% | 100% | 100% | 100% |
| discovery-03 | 66.67% | 66.67% | 66.67% | 66.67% |
| discovery-04 | 100% | 100% | 100% | 100% |
| discovery-05 | 80% | 80% | 100% | 100% |
| discovery-06 | 100% | 100% | 100% | 100% |
| discovery-07 | 100% | 100% | 100% | 100% |
| discovery-08 | 50% | 50% | 50% | 100% |
| Macro | 87.08% | 87.08% | 89.58% | 95.83% |

这次是单查询排名指标，不是多查询并集覆盖。

- OFA：discovery-08 原查询排名 13 → 7，score 0.5445707440376282 → 0.5425180196762085；相对排名提升不要求绝对分数增加。
- InstructBLIP：discovery-03 原查询排名仍为 15，score 0.5556490421295166 → 0.5247846245765686。未补齐这条遗漏，不继续调本阶段检索。
- 24/25 篇生成非空画像。PaLM-E 一次输出未通过画像校验，回退为空；该题 R@5/10 仍为 100%，没有为了成功率重新抽取。
- 画像 LLM 调用 25 次，总模型请求耗时 43.980 秒，离线 enrichment 总耗时 43.985 秒，不含文档向量生成和索引写入。
- 已逐条核对 25 条索引 paper_json 与 documents，确认保存的增强字段及文本实际进入 Chroma。

原始索引保留在 data/chroma/paper-discovery-before-rich，默认 data/chroma/paper-discovery 使用增强索引。重新评测旧索引可运行：

```bash
python -m eval.paper_discovery --index-dir data/chroma/paper-discovery-before-rich \
  --output artifacts/paper-discovery-before-rich.json
```

本地旧索引是本次实验备份，不随 Git 克隆分发。

## 验证与限制

原 306 项测试全部保留，新增 22 项，全量 328 passed。覆盖结构抽取、空字段、非法/无来源输出、依赖失败、去重、长度限制、集中序列化、旧文档和索引兼容、画像缓存复用及来源变化失效、逐篇失败回退和 Chroma round-trip。既有 API、UI、Agent、Paper Tool 回归也包含在全量测试中。

原 fixture 的 abstract 是 source_based_summary，画像继承其覆盖边界。本实验同时改变了序列化字段标签和画像内容，没有区分两者贡献；开发候选标签及小语料结果不代表通用科研检索性能。到此结束 Paper Retrieval 优化，不引入 BM25、Hybrid、Reranker、多向量、全文抽取或任何针对特定题目的补丁。
