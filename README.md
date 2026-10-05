# Multimodal Research Agent — Day 1 evidence foundation

当前状态：证据基础设施已实现；真实 Embedding / VLM 冒烟测试待用户手动运行。

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
