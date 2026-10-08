# 开发与使用指南

本指南对应 Stage G1，基于真实配置和 CLI；默认从**源码仓库根目录**执行。第一次阅读可先看 [架构总览](../architecture/system-overview.md) 与 [工程清单](../architecture/engineering-inventory.md)。本轮仅执行帮助、只读校验、完整测试和现有静态检查，未执行以下安装、准备、索引或真实模型命令。

## 1. 环境与依赖

Python ≥3.11。已有环境可直接激活；新环境按 [pyproject.toml](../../pyproject.toml) 的 optional dependency groups 安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,retrieval,vision,llm,data,api]'
```

`ui-test` 是浏览器检查的可选依赖，基础 pytest 不要求启动浏览器。本项目没有锁定全套依赖；上述安装可能下载较大的 Torch / 模型依赖。已有可用环境无需重装。希望使用 CPU 专用 Torch 时应按环境需要预先安装对应发行版，不将本机当前安装版本当成项目强制要求。

`scripts` 与 `eval` 不在 wheel 的 `app*` 打包范围，操作指南按源码 checkout 运行，不保证安装 wheel 后在任意目录直接运行这些模块。

## 2. 环境变量与安全配置

从 [.env.example](../../.env.example) 创建本地配置；已有 `.env` 时保留它：

```bash
test -e .env || cp .env.example .env
```

| 配置 | 用途 / 默认值 |
| --- | --- |
| `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` | 支持 Tool Calling 的模型；不默认替用户选择 Provider。 |
| `VLM_API_KEY` / `VLM_MODEL` / `VLM_BASE_URL` | 图片输入和 JSON 输出模型。API runtime 与真实研究执行入口要求六项均填写；填写与 LLM 相同的值不保证模型具备视觉能力。 |
| `AGENT_ENV_FILE` | API 可选环境文件路径；没有该变量时使用进程环境，不自动读取根目录 `.env`。 |
| `AGENT_RETRIEVAL_BACKEND` | 默认 `day5-controlled`；真实 Embedding 排序使用 `real`。受控检索不是检索质量结果。 |
| `AGENT_EMBEDDING_MODEL` / `AGENT_DEVICE` | 默认 Qwen/Qwen3-Embedding-0.6B / cpu。 |
| `AGENT_INDEX_DIR` | API 的文本索引目录，默认 `data/chroma/day2`，collection 为 `papers-day2-text`。 |
| `AGENT_PAPER_DISCOVERY` | 默认 `0`；`1` 才给 API 单 Agent 接入发现与查询扩展。 |
| `AGENT_PAPER_EMBEDDING_MODEL` / `AGENT_PAPER_INDEX_DIR` | 默认同名 Qwen 模型 / `data/chroma/paper-discovery`；与正文索引独立。 |

研究脚本的 corpus、index、预算、Top-K 由 [configs/cross_paper_eval.json](../../configs/cross_paper_eval.json) 控制，不直接继承 API 的 `AGENT_INDEX_DIR`。脚本 `--env-file` 会读取指定 `.env`，`override=False`，已设置的进程环境优先。

[.gitignore](../../.gitignore) 已忽略 `.env*`（保留示例）、虚拟环境、缓存、模型权重、`data/chroma/`、`artifacts/`。不要将凭据、整个环境或索引复制进验收目录；错误报告只使用脱敏字段。

## 3. Prepared Corpus 与来源校验

仓库已有固定语料，日常运行优先**校验并使用现有材料**：

```bash
python -m scripts.ingest_corpus
python -m scripts.validate_research_corpus \
  --papers-dir data/papers \
  --additional-papers-dir data/research_papers \
  --output artifacts/local-checks/corpus.json
python -m scripts.verify_worker_coverage_baseline
```

第一条没有 `--execute` 时只校验 V0.1 三篇语料；第二条校验完整五篇及来源清单，写诊断 JSON，不初始化 Embedding、不调用 Provider、不写索引。第三条核对 Stage E 292 文件及 F3.1 基线登记的 47 个受保护产物。G1 的额外 F3.1-E 文件保护记录见 [baseline.json](../acceptance/stage_g1/baseline.json)。

Evidence 页码是 PDF 页序号。`read_page` 只返回已经准备的页面，不临时解析缺失页。追加语料须有 `metadata.json`、`source_manifest.json`、`prepared_evidence.jsonl` 及哈希匹配的 PDF / 页面 / 图片。

### 重新准备 fixture：仅供未来明确需要时

以下命令会写输出材料，G1 未执行。先验证输入 PDF 版本和哈希；示例将输出放在新临时目录，不覆盖冻结语料：

```bash
python -m scripts.prepare_paper_fixture \
  --config data/fixture_configs/clip.json \
  --pdf data/papers/clip/paper.pdf \
  --output artifacts/local-checks/prepared-clip
python -m scripts.prepare_research_corpus \
  --config data/fixture_configs/blip2.json \
  --pdf data/research_papers/blip2/paper.pdf \
  --papers-dir artifacts/local-checks/prepared-research
```

注意配置形状不同：`prepare_paper_fixture` 接受直接 PaperFixtureConfig；`prepare_research_corpus` 读取含 `fixture` 和章节标注的研究配置，并生成来源清单。两者复用人工 region / crop 机制，不是自动图表检测。后者**即使不传 `--config` 也会重写指定目录来源文件**，不要把它当只读 validator。

## 4. 索引准备与核验

三种索引用途不要混用：

| 索引 | 使用者 | 数据 |
| --- | --- | --- |
| `papers-day2-text` / `data/chroma/day2` | API 的 real 文本检索 | V0.1 3 篇 / 25 条文本。 |
| `research-stage-e-text` / `data/chroma/stage-e` | 当前研究配置 | 5 篇 / 46 条文本，绑定 corpus snapshot。 |
| Paper Index / `data/chroma/paper-discovery` | `search_papers` | 25 篇元数据；论文画像是可选离线 enrichment。 |

需要新建 V0.1 或 Paper Index 时使用现有命令，以下会加载 Embedding 并写索引，**G1 不执行**：

```bash
python -m scripts.ingest_corpus --execute \
  --papers-dir data/papers --index-dir data/chroma/day2 \
  --embedding-model Qwen/Qwen3-Embedding-0.6B --device cpu
python -m scripts.ingest_papers \
  --fixture data/paper_discovery/papers.json \
  --index-dir data/chroma/paper-discovery \
  --embedding-model Qwen/Qwen3-Embedding-0.6B --device cpu
```

`ingest_papers` **没有 `--execute` 开关，正常运行即写索引**；增加 `--enrich` 还会按 Title + Abstract 调用 LLM，并用 `--profile-cache` 保存画像。API 开启发现时校验 Paper Index 的 fixture、模型和表示来源。

研究索引的 `real_retriever` 工厂在 [evaluate_cross_paper.py](../../scripts/evaluate_cross_paper.py) 中，现有真实执行 / 检索评测模式可能补建或更新其 collection；仓库没有独立的“只读 Chroma 排名校验 CLI”。先通过下面预检核对语料、配置与需求，不要把预检结果当成索引或真实检索已通过：

```bash
python -m scripts.evaluate_cross_paper \
  --config configs/cross_paper_eval.json \
  --case-id two-paper-bridge \
  --output artifacts/local-checks/research-preflight.json
```

该命令不带执行模式时返回 `not_executed`，模型调用为 0。G1 保留现有索引，未运行 `--execute` 或 `--execute-retrieval`。模型、fixture 或表示版本不匹配时不要强行共用旧索引；记录问题，未来明确授权后另建索引。

## 5. 单 Agent API/UI

配置完成后，已有演示入口如下；启动可能加载 real Embedding，Ask 会调用真实模型，G1 不启动服务：

```bash
AGENT_ENV_FILE=.env python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`，HTTP 接口为 `POST /api/agent/run`，请求含 `question`。界面返回答案、引用、可观察轨迹和指标。配置失败时 UI 仍可打开，但 runtime 不可用；UI 可访问不代表模型和索引已初始化成功。

该入口使用 `BoundedToolAgent`。它不是多论文研究 Orchestrator，不能通过此 UI 声称已经展示 Reviewer → Recovery → Synthesis。Figure 图片展示也不等于 VLM 真的分析过像素，应核对真实调用记录。

## 6. 测试与静态检查

```bash
python -m pytest -q
ruff check app tests scripts eval
ruff format --check app tests scripts eval
```

使用已有 Ruff；项目未在 dependency groups 配置 Ruff，也未配置独立 type-checker。命令检查范围沿用 `app tests scripts eval`。不要把历史 Markdown 评测标注指南中的代码块格式问题当作应改写冻结材料的理由。

相关模块可用 `python -m pytest -q tests/research` 或 `tests/research_quality` 定位，完整验收仍运行全量。测试的 Fake / Scripted Provider 验证工程契约，不代表真实研究质量；本轮完整 pytest 没有发起真实 Provider 评测。

## 7. 评测和诊断入口

| 入口 | 用途与执行边界 |
| --- | --- |
| `scripts.evaluate_cross_paper` | 默认只预检；`--scripted-smoke` 验证连线；`--execute` 真实研究；`--execute-retrieval` 真实检索对照。真实模式可能写索引。 |
| `scripts.smoke_research_planner` | 真实 Planner Smoke；正常运行会调用 Provider，不是默认只读。 |
| `scripts.smoke_async_research` | 读取已有 plan，Worker Pool Smoke；查看 `--help` 与真实/验证参数后再执行。 |
| `scripts.smoke_evidence_review` / `scripts.smoke_research_synthesis` | 输入既有中间结果；使用 `--validate-only` 做输入校验，真实模式需模型配置。 |
| `scripts.smoke_research_recovery` | Recovery 专项证据与工程 Smoke；按真实/受控模式区分，不能混入语义性能结论。 |
| `scripts.validate_quality_benchmark` | 来源、身份、分组、标注状态校验；只读输入，写新校验结果。 |
| `scripts.run_quality_benchmark` | 默认验证；`--execute` 才运行真实评测。G1 不跑 90 题。 |
| `scripts.score_quality_benchmark` | 确定性汇总离线预测与人工/AI 语义标注；不调用生产 Reviewer 或真实 Provider。 |
| `scripts.prepare_worker_coverage_comparison` | 从 F2 身份与需求生成独立请求，不用 Gold 构造 Worker 提示。 |
| `scripts.run_worker_coverage_comparison` | **实验专用，正常运行就真实调用 Provider**；先单题 Smoke，`--remaining` 再执行剩余六题。G1 仅检查帮助。 |
| `scripts.summarize_worker_coverage_comparison` | 离线逐题比较，语义标签与来源绑定；不把 covered / review passed 直接当正确。 |

离线校验与复算 F2 已有结果的完整路径示例：

```bash
python -m scripts.validate_quality_benchmark \
  --dataset data/benchmark/research_quality_v1_ai_reference \
  --output artifacts/local-checks/quality-validation.json
python -m scripts.score_quality_benchmark \
  --dataset data/benchmark/research_quality_v1_ai_reference \
  --reference-kind ai_reviewed --split all \
  --predictions docs/acceptance/quality_end_to_end/results/predictions.jsonl \
  --ai-assessments docs/acceptance/quality_end_to_end/results/ai-assessments.jsonl \
  --output artifacts/local-checks/quality-score.json
```

这里是 **AI 审核参考集**，不是人工 Gold。不要复制不存在的 `predictions.jsonl` / `human-assessments.jsonl` 占位文件名；真正的人类评分使用 `--assessments`，AI 评分使用 `--ai-assessments`。上述输出位置与原始结果分离，G1 不改 F2 原始记录。单题真实质量诊断与正式 Benchmark 也应分开。

## 8. 常见问题与排查

| 症状 | 排查顺序 |
| --- | --- |
| module not found / 相对路径不存在 | 确认位于源码仓库根目录，激活正确 `.venv`，使用 `python -m scripts...`；不要把 Windows UNC 路径直接交给 Linux CLI。 |
| FileNotFoundError / invalid_input | 检查真实 dataset、predictions、assessments 路径和参数类别；评分结果中的 input_stage / input_errors 可定位，不代表模型已调用。 |
| API 503 / runtime unavailable | 检查六项环境变量是否非空、AGENT_ENV_FILE 是否存在、检索模式及索引是否匹配。不要打印 API Key。 |
| multiple_actions / protocol_failure | 查看 Worker rejection 与下轮 feedback、max_protocol_corrections、模型预算；一次违规不执行任何被拒工具。重复耗尽是执行失败。 |
| 论文已发现但无正文 | 元数据候选存在不代表 Evidence 可用；unavailable / empty 是真实边界，不应补出引用。 |
| 空检索 / 页未准备 | 看 issue、paper scope、已准备页；空结果是业务结果，允许模型换查询或保留不足，不能自动当 Chroma 故障。 |
| dependency_failure | 看脱敏的 Provider 状态和 transient/permanent 分类、attempts、超时；不与语义审核 rejected 混为一谈。 |
| 研究执行时间长 | 分开看资源槽排队、真实尝试、重试、protocol correction、Recovery 和 drain；外层取消不会强杀已运行线程。 |
| 结构引用通过但事实不完整 | 身份检查不证明语义蕴含，review passed 不证明需求完整；使用独立语义评分和原证据复核。 |
| Coverage 实验“支持”仍出错 | 核对诊断 sidecar、来源摘录、各属性与阶段；结构化 covered 不能自动当 Gold。 |

## 9. 维护边界

保留 Stage E 冻结清单、F2 原始结果、参考标签、F3.1-E 对照证据。日常诊断输出放 `artifacts/`，接受用户审核后再选择性归档。不要把现有未跟踪诊断材料当垃圾删除。G1 没有清理或移动代码、没有切换默认 Worker、没有启动下一阶段。
