# Multimodal Research Agent

当前状态：Day 3 已把语料能力封装为三个工具，并建立 Observation 与执行记录；未接 LLM、未实现 Agent Loop。各阶段真实模型验收状态见下方清单。

本阶段没有 Agent Loop、自动图表检测、跨论文比较或最终问答 API。现有 SingleStepToolCaller 保留原样。

## 已建立的接口

- 不可变 TextEvidence / FigureEvidence；paper_id、PDF 页码、稳定 evidence_id。
- RetrievedEvidence 单独保存 retrieval_score / rerank_score。
- TextRetriever.search_text(query, paper_ids=None, top_k=3)。None 搜全库，空列表搜零篇。
- ChromaTextVectorStore 保留完整 metadata，查询时下推论文筛选。
- VisionLanguageClient、OpenAI-compatible vision adapter、FigureAnalyzer。
- FigureAnalysis 分开 observations、interpretation、evidence_used、limitations，保留 provider usage/finish_reason。

## 复用来源与边界

app/retrieval 下的 embedding、vector_store、retriever 适配自 Personal RAG 的 src/rag 同名实现。新接口使用本项目 app namespace，没有 sys.path 注入、跨仓库 import 或 shared package。两个旧仓库不变。Mini GPT Service 的分层和 fake 测试方式作为设计参考，没有迁移字符模型运行时。

普通模块导入不会加载 torch、transformers、Chroma 或模型 SDK；仅构造相应真实适配器时使用这些可选依赖。

## BLIP 最小数据

来源：[BLIP arXiv v2](https://arxiv.org/abs/2201.12086v2)。data/papers/blip 含官方 PDF、metadata.json、人工选择的第 2/3/4 页文本区域和 Figure 3 图片。

page 是从 1 开始的 PDF 页序号。Figure 3 位于 PDF 第 4 页，是 CapFilt 学习流程图；它本身不是性能比较图。

文本按来源页提取后保留段落边界；不拼接全文后反推页码。evidence_id 对固定论文版本、页码、模态与段落文本计算 hash；语料/切分改变时 ID 可以改变，不承诺跨版本永久稳定。Figure 使用固定图编号。

scripts/prepare_blip_fixture.py 保存人工指定的文本区域和图表裁剪坐标，校验 PDF SHA256 后重建。需要 data 可选依赖，不自动下载、不检测图表、不启动模型。裁剪已经人工检查。

## 普通测试与本地 Chroma 测试

在项目根目录，已有 Python 3.11+ 环境可使用：

```bash
python -m pip install -e '.[test]'
python -m pytest
```

普通测试使用 FakeEmbedding、FakeVectorStore、FakeVisionLanguageClient，不调用真实模型。Chroma 未安装时，本地数据库集成测试自动跳过。仅安装 Chroma 就能执行该测试，无需模型权重：

```bash
python -m pip install 'chromadb>=1.0,<2.0'
python -m pytest tests/integration/test_chroma.py
```

## 用户手动运行：真实文本检索

安装真实检索依赖时请根据自己的硬件选择 PyTorch；CPU 环境先装 CPU wheel，避免安装无用 CUDA 运行库。

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[retrieval,vision,test]'
python -m scripts.smoke_test_day1 --mode text
```

首次运行可能下载 Qwen 模型，CPU 推理需要时间。索引位于独立的 data/chroma/day1，使用 upsert，不删除旧 collection。换模型或更改语料时使用新的 --index-dir，避免混用旧向量。

查看三个问题的 Top-K，人工确认正确证据、页码和来源；这是冒烟测试，不是有统计意义的 Recall@K。

## 用户手动运行：真实 Figure 分析

设置 VLM_API_KEY、VLM_MODEL、VLM_BASE_URL。必须选择支持图片输入及 Chat Completions JSON object 输出的模型/服务；未预设模型名，也未验证任何特定供应商。密钥只通过当前进程环境传入。SDK 不自动重试，默认超时 60 秒。

```bash
export VLM_API_KEY='你的密钥'
export VLM_MODEL='你选择的视觉模型'
export VLM_BASE_URL='你的服务接口地址'
python -m scripts.smoke_test_day1 --mode figure
```

默认问题：“Figure 3 为什么证明 BLIP 性能更强？请核实问题前提。”

人工核对：实际图片确实送入模型；observations 来自图像；interpretation 使用图注与正文；模型没有把流程图视为性能证明；limitations 如实说明局限。结构验证只能检查字段和证据 ID，不能保证语义正确。

脚本打印结果并把报告保存至 D:/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体，报告始终标为 requires_manual_review。可用 --output-dir 指定其他位置。不自动宣称 Day 1 Passed。

## 验收状态

- [x] Evidence 模型与身份稳定性测试。
- [x] paper_id/page/evidence_id 的本地 Chroma 往返验证。
- [x] 论文过滤下推到向量查询。
- [ ] 用户确认三个真实文本查询的相关证据进入 Top-K。
- [ ] 用户确认真实 Figure 已进入真实 VLM。
- [ ] 用户核对联合分析及 observation/interpretation 区分。

下一步：由用户运行两个真实 smoke test，再记录已确认/失败原因。未完成真实验证前不进入 Agent Loop。


## Day 2：统一的三论文语料库

当前状态：统一语料、摄取和筛选的离线验证已完成；真实语义检索效果待手动冒烟验证。未实现 Agent Tool 或 Agent Loop。

| 简称 | 固定论文 ID | 选定 PDF 页 | 文本 Evidence | Figure |
| --- | --- | --- | --- | --- |
| clip | clip-2103.00020v1 | 2、4 | 5 | Figure 1，PDF 第 2 页 |
| blip | blip-2201.12086v2 | 2、3、4 | 11 | Figure 3，PDF 第 4 页 |
| flamingo | flamingo-2204.14198v1 | 4、9、10、11 | 9 | Figure 3，PDF 第 9 页 |

CLIP 来源：[官方 arXiv v1](https://arxiv.org/abs/2103.00020v1)
Flamingo 来源：[官方 arXiv v1](https://arxiv.org/abs/2204.14198v1)

三篇都采用同一个 PaperMetadata schema，含 schema_version、paper_key、paper_id、title、source_url、pdf_url、pdf_sha256、page_numbering、pages、figures 和 smoke_queries。旧单数 figure 已统一为 figures 列表，Day 1 脚本已适配。

paper_key 用于 clip/blip/flamingo 简称，paper_id 保留论文版本。BLIP 原有 11 个证据 ID 全部保留。TextRetriever 接收 corpus.paper_aliases 时，会在调用向量库前将简称解析为版本 ID；存储层执行 where 查询，不在 Top-K 后过滤。

### 人工配置和通用准备机制

人工选区位于 data/fixture_configs/{clip,blip,flamingo}.json，通用执行逻辑位于 app/evidence/preparation.py。仍然不是通用 PDF Parser，也没有自动 Figure Detection。PDF 原文和图片保留为学习语料，并记录原论文来源。

在安装 data 可选依赖后，可按固定版本 PDF 重建，例如：

```bash
python -m pip install -e '.[data]'
python -m scripts.prepare_paper_fixture --config data/fixture_configs/clip.json --pdf data/papers/clip/paper.pdf --output data/papers/clip
```

准备前核对 PDF SHA256、页码和矩形边界。生成的页文件和图片记录各自 SHA256。原有 prepare_blip_fixture 现在只是读取 BLIP 配置的兼容入口；请从项目根目录使用 python -m scripts.prepare_blip_fixture。

### 统一加载和摄取

load_paper_corpus(data/papers) 返回 PaperCorpus：text_evidence、figure_evidence、paper_aliases 和证据来源映射。corpus.locate(evidence_id) 可定位原 PDF、PDF 页码、页级选区文本；Figure 还包含图片和图注。

加载时验证 PDF/页文件/图片哈希、相对路径边界和 Figure 相关正文，避免读取被改动的 fixture 或错误关联。pages 文件仅是人工选择的原文区域，不是完整论文页全文。

默认只检查语料，不运行模型，也不写索引：

```bash
python -m scripts.ingest_corpus
```

用户需要建立真实索引时再执行：

```bash
python -m scripts.ingest_corpus --execute
```

所有论文都走 ingest_text_corpus。默认独立索引 data/chroma/day2、collection papers-day2-text。重复摄取同一固定语料使用稳定 ID 的 upsert，不增加重复记录；Figure 可定位但不写入文本向量库。修改语料/配置或 embedding 模型时使用新的 --index-dir；当前不实现删除旧语料的同步机制。

### Day 2 真实检索冒烟

data/smoke/day2_queries.json 包含 8 个问题：每篇 2 个单论文问题，以及 CLIP vs BLIP、BLIP vs Flamingo 两个跨论文问题。跨论文问题明确列出各论文的检索请求及预期页码，不让模型生成比较答案。

用户手动运行：

```bash
python -m scripts.smoke_test_day2 --top-k 3
```

脚本通过统一摄取建立/更新真实文本索引，然后执行 10 次按论文限定的检索。逐条打印 paper_id/page/evidence_id/score/原文预览，并保存可追踪来源。跨论文问题须分别检查每篇所需证据，不能仅因出现两个 paper_id 就判定成功。

结果默认保存到 D:/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体，标记 requires_manual_review。本阶段没有自动运行真实 Embedding 或 VLM。

### Day 2 验收边界

- [x] Corpus Correctness：三篇共 25 条 TextEvidence 和 3 条 FigureEvidence，使用同一 metadata contract。
- [x] Traceability：每条证据可定位到源 PDF 和选定页；Figure 可定位图片与图注；重建 metadata 和资源哈希一致。
- [x] Filtering：Fake/固定向量及真实本地 Chroma 验证三篇单选、clip+blip 和全库；BLIP-like 查询限定 clip 后只返回 CLIP，即使 Top-K=1。
- [ ] Retrieval：尚未运行真实 Embedding；每篇两题的 Top-K 相关性及两组跨论文证据完整性待用户确认。

单元/数据库测试只证明结构、过滤和幂等性，不证明真实语义检索质量。未完成最后一项前不宣称 Day 2 Passed。


## Day 3：Domain Capability → Agent Tool

三个工具已接入同一个 ToolRegistry，并通过 ToolExecutor 作为面向 Agent 的执行入口。沿用的 registry.execute 是旧版原始执行接口；它的异常契约保留兼容性。新 Agent 集成请使用 ToolExecutor，不要绕过观察/错误/脱敏/Trace 边界。

```python
from pathlib import Path
from app.evidence.corpus import load_paper_corpus
from app.tools.executor import ToolExecutor
from app.tools.paper_registry import create_paper_registry

corpus = load_paper_corpus(Path("data/papers"))
# retrieval 为已有 TextRetriever，或测试中的 FakeRetrieval。
executor = ToolExecutor(create_paper_registry(corpus, retrieval))
observation = executor.execute(
    "search_text",
    {"query": "What does the Perceiver Resampler do?", "paper_ids": ["flamingo"], "top_k": 1},
    tool_call_id="step-1",
)
```

### Observation 规范

公共字段：status、tool_name、summary、payload，另有可选 issue（code、retryable）。成功与空结果拥有各工具自己的 payload 结构：

- search_text：paper_ids、evidence；每项含 evidence_id/paper_id/page/text/retrieval_score/rerank_score。
- find_figure：figure；含 evidence_id/paper_id/page/figure_id/caption/asset_ref。
- read_page：paper_id/page/coverage/prepared_pages/evidence/figures。coverage 固定为 prepared_regions_only，绝不声称已读取完整 PDF 页。

| 情况 | status | issue.code | retryable |
| --- | --- | --- | --- |
| 找到证据 | success | 无 | — |
| 未搜到证据、图未准备、页未准备 | empty | empty_result | false |
| 参数非法 | error | invalid_arguments | false |
| 论文未知 | error | unknown_paper | false |
| 存储、embedding 或证据访问依赖故障 | error | dependency_failure | true |
| 程序或返回数据不符合契约 | error | internal_failure | false |
| 工具未注册 | error | unknown_tool | false |

未准备页面还会在 payload 中给出 reason=page_not_prepared 和 prepared_pages。例如 Flamingo 第 35 页返回 empty，不触发 PDF 解析；未知论文返回 error，不混同为无检索结果。retryable 是供未来循环使用的分类提示，当前执行器不自动重试。

### 资源和安全边界

Figure 的公开资源引用形如 asset:ev_<hash>。FigureAssetResolver 在后端把已登记引用映射为真实路径，不把调用者字符串当路径，不允许任意文件访问。观察里不返回 asset_path、源 PDF 路径、Chroma 细节或异常堆栈。

公开 payload 由明确的 Evidence DTO 投影生成，异常只映射为固定说明。ToolExecutor 同时脱敏公开观察和 Trace 的参数：敏感字段、进程环境中的 secret 值、常见 API key 模式以及本地路径。不会修改原语料内容，也不会读取其他项目的 .env。

每次 execute 留下一条 ToolExecutionTrace：tool_call_id、tool_name、arguments、status、latency_ms、result_count。success/empty/error 都记录；不记录模型内部推理。read_page 的 result_count 是文本证据与图证据数量之和。

查询返回的身份或内容若不属于当前语料快照，或者违反指定论文范围，会被拒绝为 internal_failure，避免把旧索引或错误实现当成当前证据。

### 不接模型的手动演示

```bash
python -m scripts.demo_day3
```

默认 fixture 模式使用真实 Flamingo 原文和固定向量排名，不调用 Embedding 模型、VLM 或 LLM。它验证的是工具协议和证据扩展流程，不是语义检索质量。

由人预先选定的两步调用：

1. search_text，Flamingo，Top-K=1：返回第 9 页关于 Perceiver Resampler 和固定大小视觉 tokens 的原文段落。
2. read_page，Flamingo，第 11 页：返回两段准备好的原文，包含 64 个输出以及降低 vision-text cross-attention 复杂度的动机。

报告保存实际 Observation 与 Trace 到本项目 D 盘产物目录。这里没有自动下一步决策；第二次调用是人为指定。

若用户要用已有 Day 2 索引检查真实 Embedding 的返回，可显式运行：

```bash
python -m scripts.demo_day3 --backend real
```

该选项不会接 LLM，也不会自动重建索引，需要先准备好 Day 2 索引；真实检索可能返回其他相关段落。本阶段没有执行此选项。

### Day 3 验收

- [x] 三工具的正常、空、非法参数、未知论文和依赖失败测试。
- [x] 搜索范围、Figure 精确匹配、准备页与未准备页边界测试。
- [x] 观察和 Trace 不暴露测试中的路径、异常堆栈与 API key。
- [x] 真实本地 Chroma + 固定向量的工具集成测试。
- [x] 无 LLM 的两步人工调用演示跑通。

下一步：Day 4/5 再让模型根据 Observation 决定下一次调用。Day 2 真实语义检索验收仍是独立事项，不因本阶段协议测试通过而自动完成。
