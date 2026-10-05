# Multimodal Research Agent

当前状态：Day 2 三论文语料基础设施已实现，79 项离线测试通过；真实模型验收状态见下方各阶段清单。

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
