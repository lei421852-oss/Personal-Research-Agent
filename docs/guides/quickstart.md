# v1.0.0 快速启动与复现

本指南从 **源码仓库根目录** 操作，命令对应当前真实代码。G4.3 只核验命令、现有资源及离线工程行为，不在本轮下载模型、安装新环境、重建索引或重跑真实研究。

## 1. 获取源码与安装

Python ≥3.11，推荐 Linux / WSL。完整依赖未锁定，机器与 Provider 差异会影响结果和耗时。

```bash
git clone git@github.com:lei421852-oss/Personal-Research-Agent.git
cd Personal-Research-Agent
git checkout v1.0.0
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,retrieval,vision,llm,data,api]'
```

可选浏览器检查另外安装已有 extras；Ruff 是检查工具，未加入冻结依赖定义：

```bash
python -m pip install -e '.[ui-test]' ruff
python -m playwright install chromium
```

Torch / Transformers 属于检索依赖，下载可能较大。仅启动工作台和回放可使用 `.[test,llm,api]`，真实检索再安装 retrieval；图片和 PDF 准备分别依赖 vision / data。项目操作要求源码 checkout，不能把安装 wheel 视作完整论文资源、脚本和静态工作台的分发方案。

## 2. 先看无需模型的历史演示

```bash
python -m scripts.launch --check
python -m scripts.launch
```

打开 `http://127.0.0.1:8000`，选择顶部 G2 或 G2.1，再点击“历史回放”。已有中文副本、报告和引用从提交文件读取，0 次 Provider 调用。缺模型配置时不能启动真实研究，但不妨碍回放。

`--check` 的成功仅表示 UI 可启动，检查 JSON 中的各项 readiness；它不代表已经真实连接 Provider 或执行 Embedding。修改端口可使用 `--port 8001`。

## 3. 配置真实模型服务

保留既有 `.env`，新环境再从 [.env.example](../../.env.example) 创建：

```bash
test -e .env || cp .env.example .env
```

填入 `LLM_API_KEY / LLM_MODEL / LLM_BASE_URL`。主 Agent 需要支持 Function / Tool Calling 的 OpenAI-compatible Provider。现有 DeepSeek 文本配置可以用于主决策与中文适配；密钥通过本地文件或进程环境提供，不放入代码和 Git。

`VLM_API_KEY / VLM_MODEL / VLM_BASE_URL` 必须来自真正支持图片输入的服务。当前 `prepare_resources` 仍要求六项配置完整，因此正文检索和多 Agent 研究装配也会检查 VLM 配置，即使某次执行没有使用图像。不要填入虚假配置来制造 ready，也不要把相同的文本模型配置视作支持 VLM。

新工作台默认读取仓库根目录 `.env`，进程环境优先。示例里的 `AGENT_RETRIEVAL_BACKEND / AGENT_INDEX_DIR / AGENT_PAPER_DISCOVERY` 主要服务旧 `app.main` 入口；当前工作台的研究索引与预算来自 [cross_paper_eval.json](../../configs/cross_paper_eval.json)，不能靠打开旧开关代替当前资源准备。

## 4. 校验已提交语料

```bash
python -m scripts.validate_research_corpus \
  --papers-dir data/papers --additional-papers-dir data/research_papers \
  --output artifacts/local-checks/corpus.json
python -m scripts.verify_release
```

发布检查保留原 Stage E 清单与历史标签。用户明确批准仅更新根 README，`verify_release` 核验其历史 SHA、发布 SHA、其余 291 个文件和 47 个受保护产物。原 `verify_worker_coverage_baseline` 在 v1.0.0 工作区仍会对 README 报差异；它的冻结源码未修改。原 `run_research_demo` 等严格检查旧完整快照的历史脚本应在相应旧版本使用，当前用户入口为 `scripts.launch`。

固定 Prepared Corpus 是 5 篇、46 文本、5 Figure；Paper Catalog 是 25 篇元数据。这里不下载新论文，不自动全文解析，也不调用模型。

## 5. Embedding 缓存与研究索引

工作台使用 `Qwen/Qwen3-Embedding-0.6B` 的本地 Hugging Face 缓存；运行时明确使用本地文件，不自动下载。新机器可在**准备阶段**显式下载：

```bash
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(repo_id="Qwen/Qwen3-Embedding-0.6B")
PY
```

标准缓存应包含 `refs/main` 和对应 snapshot 的 tokenizer、config 与模型权重。可以用进程环境 `HF_HOME / HF_HUB_CACHE` 指定缓存位置，但须保持上述结构。当前研究配置为 CPU；不在发布阶段更换模型。

正文索引为 `data/chroma/stage-e` 的 `research-stage-e-text`。它不随 Git 分发。已有正确索引只做启动预检，不重建；**新机器缺索引时**，可以显式复用现有准备工厂：

```bash
python - <<'PY'
from app.research.workbench_dependencies import WorkbenchDependencies
from scripts.evaluate_cross_paper import real_retriever
resources = WorkbenchDependencies()
config = resources.config()
corpus, snapshot = resources.corpus(config)
real_retriever(config, corpus, snapshot)
print("Research evidence index prepared:", len(corpus.text_evidence), "text records")
PY
```

该准备调用使用真实 Embedding，并可能创建 / 写入本地索引；不调用研究 LLM。绑定不一致时会拒绝共用，不要删除或覆盖历史索引来绕过校验。CLI `evaluate_cross_paper` 的默认预检不会建索引，不能将 `not_executed` 当作资源准备成功。

## 6. 可选论文发现索引

`search_papers` 使用独立的 `data/chroma/paper-discovery`，不与正文向量混用。新机器可按需创建：

```bash
python -m scripts.ingest_papers --device cpu
```

该命令会生成真实向量并写索引。原始 metadata 也可被检索；希望使用离线画像时，准备阶段显式调用：

```bash
python -m scripts.ingest_papers --enrich --env-file .env --device cpu \
  --profile-cache artifacts/paper-profiles.json \
  --report artifacts/local-checks/paper-enrichment.json
```

`--enrich` 会调用 LLM；查询时不会抽取画像。画像缓存与对应索引需要一起保留，`load_index_catalog` 会逐篇检查相等及来源。新抽取结果可能不同，不承诺复现历史 24/25 非空画像或固定 Recall。没有 discovery 索引时，一般解释 / 已知论文研究可走其他实际路径；真正请求发现工具会反馈资源失败，不伪造候选。

## 7. 开始真实研究

准备资源后再次 `python -m scripts.launch --check`，确认相关项目 ready，再启动服务。输入问题即可，不需要选论文。

- 简单文本：`What training objective does CLIP use to align images and text?`
- 连接机制：`比较 BLIP-2 与 Flamingo 的视觉语言连接机制，并引用论文证据。`
- 复杂任务：使用[Demo C](../demos/demo-cases.md)的逐论文、多维度研究问题。

真实执行会产生 Provider 调用；这些示例不保证固定动作路径。线性与协作树依据实际事件切换。中文字段翻译失败保留原回答；研究失败、证据不足和取消分别显示。

## 8. 测试的两种口径

无 Provider、无本地向量索引的离线工程子集：

```bash
python -m pytest -q -k 'not test_index_catalog_is_read_only_and_matches_all_records'
```

这明确排除 **1 项历史真实 Paper Index 绑定验收**，不能称为完整测试。该项核验原有 25 个索引记录和 24 个非空画像，是本地历史资源校验；新抽取结果不必复制原数量。

具备原验收资源时运行完整回归：

```bash
python -m pytest -q
ruff check app tests scripts eval
ruff format --check app tests scripts eval
```

基本 pytest 不调用真实 Provider；Chroma round-trip 使用测试数据。浏览器脚本使用 Fake 注入检索，不依赖真实向量索引，可独立运行：

```bash
python -m scripts.check_react_workbench_ui \
  --output-dir artifacts/react-workbench-ui-check
```

该检查验证 Scripted 主策略、真实领域链与历史回放，不是新真实模型实验。支持 `PLAYWRIGHT_BROWSERS_PATH` 和可选 `--font-file`。

## 9. 常见问题

| 表现 | 检查方向 |
|---|---|
| 只能回放，不能开始 | LLM 配置及 `main_agent_ready`；UI ready 不等于真实研究 ready |
| 正文工具失败 | 六项 Provider 配置、Embedding 缓存、Stage E 索引与 snapshot 绑定 |
| 发现工具失败 | Paper Index / profile cache 是否一致，是否误用正文索引 |
| 论文有元数据但答不出正文 | 正文未准备，属于真实证据边界 |
| 一次提出多个动作 | 整批不执行，最多 2 次策略纠正，耗尽后明确失败；不截取第一项 |
| 两篇论文问题仍为线性 | 主 Agent 没有启动多 Agent 工具，并非 UI 漏画 |
| 中文未生成 | 查看语言转换与共享预算；权威原文仍保留 |
| 取消后仍等待 | 已进入同步 SDK / Embedding 的线程需要收尾，不是硬终止 |
| 新环境完整 pytest 的索引项失败 | 缺历史资源；按上述离线子集验证，并明确分母，不修改测试预期 |

没有注册 / 多会话持久化；历史在单进程内，重启会丢失当前内存任务。前端改动强制刷新即可，后端改动需要重启。更多模块说明见[开发指南](development.md)；其中 G1 的旧单 Agent 小节是历史入口，默认用户入口以本指南为准。
