# V0.2 Phase 1 · Hierarchical Retrieval

本阶段新增 Paper Discovery：开放问题先发现候选论文，再在选定论文范围内访问现有正文证据。单 Agent、原有限步 Loop 和引用规则保留，不引入规划器、评审器、并发、重排或完整跨论文研究流程。

```text
Open research question
→ search_papers(query, top_k)
→ RetrievedPaper metadata + evidence availability
→ LLM selects paper_ids
→ search_text(query, paper_ids, top_k)
→ TextEvidence / empty(unavailable)
→ next action / finish
```

## 领域与索引

- `PaperDocument`：paper_id/title/abstract/authors/year/venue/keywords，不依赖数据库、FastAPI 或 Tool。
- `RetrievedPaper`：paper、score、连续 rank。PaperRetriever 校验 query/K、去重，不重排。
- `ChromaPaperVectorStore` 复用现有 Chroma 连接与 cosine 配置，使用独立 `papers-v02-metadata` 集合及目录。完整模型序列化到 metadata，按 paper_id upsert。
- 检索文本仅为 `title + abstract + keywords`；不包含测试标签、查询或正文证据。
- 记录并校验 fixture hash 和 Embedding 模型身份，禁止无提示混用其他模型/fixture 的索引。

## Fixture 与标注边界

[data/paper_discovery/papers.json](../data/paper_discovery/papers.json)包含 25 篇论文：CLIP、BLIP、Flamingo、BLIP-2、Frozen、LLaVA、MiniGPT-4、InstructBLIP、ALBEF、ViLT、ALIGN、SimVLM、VLMo、CoCa、PaLI、PaLM-E、BEiT-3、Kosmos-1/2、ImageBind、Otter、LLaVA-1.5、SigLIP、GIT、OFA。

标题、全体作者和提交年份从固定 arXiv 版本核对；每项提供原始页面 URL 和原摘要 SHA-256。abstract 是来源摘要的简述，**不是原摘要逐字全文**，以 `source_based_summary` 显式标记。venue 使用已核实的 `arXiv preprint`，不猜测会议。keywords 全为空，不为评测题添加答案词。

原有 Evidence Corpus 仍为 3 篇论文、25 TextEvidence、3 FigureEvidence，未扩充。新目录的其余 22 篇只有发现元数据。

[queries.json](../data/paper_discovery/queries.json)固定 8 条开发查询及候选相关性标注，在第一次真实检索前写定；这些标注未作为正式人工 Ground Truth 批准。召回衡量该小型 fixture 的候选覆盖，不代表文献范围的总体召回。

## 工具与可用性

`search_papers` 复用 Registry/Executor/ToolObservation。公开候选仅包含 ID、title、year、venue、score、短摘要以及发现/证据可用标记；authors 等完整字段仅存于领域模型/索引。空结果为 empty/empty_result。

- 用户明确指定一个或多个已知论文：使用目录 ID，直接 scoped search_text，不做无意义 discovery。
- 开放问题且目标未知：模型先 discovery，再选择论文范围。
- Controller 不按关键词选论文，也不自动替换 query/scope。
- 目录结果不进入 `observed_evidence_ids`，不能把 paper_id 当 evidence_id 引用。
- 无正文候选：search_text 返回 `status=empty`、`issue=empty_result`、`availability_status=unavailable`、`unavailable_paper_ids` 与空 evidence，不访问不相关论文补结果。
- 混合范围：仅检索调用方选定的可用子集，返回 partial 并明确列出缺失论文。元数据存在不等于正文可用。

Decision Context 包含目录和实际 discovery Observation 派生的候选列表；仍从现有 State 构造，没有重复状态模型。默认 V0.1 运行模式保留；通过显式配置启用新工具。

## 启动与检查

从源码仓库根目录、已安装 V0.1 retrieval 依赖的环境运行：

```bash
python -m scripts.ingest_papers --device cpu
python -m eval.paper_discovery --output artifacts/paper-discovery-evaluation.json
```

使用现有 `Qwen/Qwen3-Embedding-0.6B`。默认索引目录 `data/chroma/paper-discovery`，不会写入 `data/chroma/day2` 的 Evidence Index。重复 upsert 保持 paper_id 唯一；改变模型或 fixture 的论文集合时使用新的目录。

开启 API 中的论文发现，在本地 `.env` 设置：

```ini
AGENT_PAPER_DISCOVERY=1
AGENT_PAPER_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
AGENT_PAPER_INDEX_DIR=data/chroma/paper-discovery
```

```bash
AGENT_ENV_FILE=.env python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

论文与正文模型一致且正文检索为 real 时复用同一 Embedding 实例。Paper Index 未准备或来源不匹配时配置失败，不静默退化为 Fake。

显式真实两题冒烟（会调用模型，非默认测试）：

```bash
python -m scripts.smoke_paper_discovery --env-file .env \
  --output artifacts/paper-discovery-agent-smoke.json
```

此入口使用真实 Paper 和现有真实 Text Index，分别观察开放问题与已知 CLIP 问题。检索路径的成功不等于答案语义完整，保留真实 stop/status/trace。

## 评测协议

每题只搜索一次 Top-10，取前 5 与前 10 计算：

$$Recall@K=\frac{|TopK\cap RelevantPaperIDs|}{|RelevantPaperIDs|}.$$

总分为 8 题的宏平均。真实执行使用 Qwen Embedding 与 Chroma，不使用 FakeEmbedding。结果保留 query、相关 ID、rank/score、命中列表、耗时和 fixture/query hash。

本轮宏平均 Recall@5=0.8708、Recall@10=0.8958。未根据这些结果调整关键词、摘要、阈值或模型。缺失命中保留，开发标签有待进一步人工审阅；不作为完整 Agent 或三系统 QA Benchmark。

## 本阶段不包含

多 Agent、Planner/Reviewer/Synthesis、异步编排、线程池、Semaphore、自动重试系统、Checkpoint、BM25/Hybrid、Paper Reranker、Image Retrieval、高级 author/year/venue filter、PDF 自动下载与完整跨论文研究。

V0.1 `eval/datasets/dev.json` 与 `test.json` 保持原样。V0.1 本地 tag 保留，不创建 V0.2 tag，不推送远程。

## 当前验收记录

全量 284 项测试通过（V0.1 257 项全部保留，新增 27 项），Ruff、121 个 Python 文件的格式检查及差异检查通过。read_page/find_figure 对目录已知但未准备的论文也返回 empty/unavailable，不误报为未知论文，不临时解析 PDF。

两条真实 Agent 冒烟使用真实 LLM、真实 Paper Embedding/Chroma 和已有真实 Text Embedding/Chroma：

- 开放问题：search_papers(top_k=10) → 模型选 Flamingo → scoped search_text → finish，2 次工具 / 3 次模型；引用均来自准备好的 Flamingo 页码。
- 已知 CLIP：直接 scoped search_text → finish，1 次工具 / 2 次模型；没有调用 search_papers。

这是实际路径检查，不是完整语义评测或普遍成功率；原始答案与引用保留供人工核查。
