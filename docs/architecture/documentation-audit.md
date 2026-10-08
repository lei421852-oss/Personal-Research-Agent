# Stage G1 文档与目录审计记录

审计基于当前源码与文件状态；原始记录见 [baseline.json](../acceptance/stage_g1/baseline.json)。开始分支为 `feature/stage-f3-1-worker-coverage`，HEAD 为 `3be3a68a87c72a22456908c9b655afcfc8c422e5`；原有 tracked 文件无修改，有 13 个未跟踪文件，全部保留。

## 已处理问题

| 发现 | 本轮处理 | 保留边界 |
| --- | --- | --- |
| README 集中描述单 Agent，并串联多个历史阶段，缺少当前研究链路总览 | 新增 [系统架构](system-overview.md)，区分 API/UI、默认研究和实验入口 | README 为 Stage E 冻结文件，不改原内容。 |
| 25 篇候选、3 篇演示与5篇研究语料容易混淆 | 工程清单及开发指南分别记录三种规模、collection 和配置 | 不扩充语料，不重建索引。 |
| 早期文档中的“不含 Reviewer / Recovery / Synthesis”可能被当成当前限制 | 新增 [文档导航](../README.md)，按阶段解释状态 | 不改写历史验收结论或重写历史文件。 |
| 像素输入能力与文本/图注研究验收容易混为一谈 | 增加能力与验证边界表；Reviewer 不读像素，七题实验 VLM=0 | 不否认既有 image_url 适配器；不宣称当前研究视觉质量已验证。 |
| `.env` 自动读取方式、脚本副作用不同 | 记录 API 的 AGENT_ENV_FILE，研究脚本 override=False；逐项标明写索引/模型调用 | 不运行真实 Provider；不执行 ingest 或 preparation。 |
| 评分示例文件名容易造成 FileNotFoundError | 使用真实已保存预测与 AI 评分路径，区分 --assessments / --ai-assessments | 不改 F2 原始评分、参考集或人工身份。 |
| Coverage Worker 可能被误认为已默认替换 | 新文档明确显式实验工厂，默认入口仍为 PaperResearchWorker | 不改变生产默认行为。 |
| 碎片化入口缺少当前命令依据 | 核对18个 CLI 的 --help，新增使用指南和入口风险说明 | 不虚构“一键研究服务”或不存在的只读索引校验 CLI。 |

## 历史材料中需要继续标注的风险

1. 冻结 README 的“当前全量 756 项测试”是 2026-10-08 当时状态。本轮实际数量见 G1 日志；不修改冻结原文。旧报告的测试数、耗时和模型配置均只能代表其原执行。
2. README 和各阶段文档仍具有教学迭代结构；现有新导航可以解读，但首次从根 README 进入的人仍可能看到过期“当前”措辞。若未来更新根 README，应另行解除该文件冻结约束并建立新版本文档，而不是静默重写 Stage E 指纹。
3. 图像分析 transport 已实现，真实研究评测没有由此得到独立视觉质量结论；保留历史 Smoke 记录及其条件，不将其转换成新 Benchmark。
4. pyproject 包版本仍为 0.1.0，仅包含 app*；尚无完整锁文件或源码脚本 wheel 打包保证。这里只记录，未改打包和依赖策略。
5. 当前没有独立 type-checker 配置，不新增工具。Ruff 检查沿用 Python 目录范围。
6. AI 参考集和七题已知失败对照不能冒充盲测人工 Gold 或泛化结论。原标签与有争议的评分解释保留。

## 目录组织与保留决定

实际主要目录为 `app/{agent,api,evidence,llm,papers,research,retrieval,tools,vision,web}`、`eval/{datasets,runners,metrics,research_quality}`、`data/{papers,research_papers,paper_discovery,fixture_configs,benchmark}`、`scripts`、`tests`、`configs`、`docs`。新增 `docs/architecture` 和 `docs/guides` 是文档目录，不移动生产代码。

- `docs/acceptance/`：保留按阶段的验收和可复核产物。本轮新增 `stage_g1`，不覆盖 Stage E / F2 / F3。
- `docs/diagnostics/quality_failures_2026_10_09/`、原未跟踪导出脚本与测试：已有工作，保持内容和未跟踪状态。
- `artifacts/`、本地索引、虚拟环境、缓存：沿用忽略规则，不复制进用户交付目录。只选择 G1 报告、文档和校验日志作为交付。
- `.env` / API Key：现有忽略规则足够；不读取或记录真实值，不改冻结 `.gitignore`。

本轮不删除重复历史文档，不清理未知文件，不将各阶段报告合并后删掉原件。未来可考虑在不改变历史记录的前提下维护持续更新的总览；涉及冻结文件或未知用途材料的整理仅作为建议。

## 检查方式与口径

新增文档的本地链接逐项检查存在性；帮助日志证明模块和参数真实存在。准备、索引、启动服务及真实实验命令只进行静态/帮助校验，不能写成已经真实执行。只读语料验证和安全预检可实际运行，结果另存 G1 目录。

针对原 README 与十份主要模块文档扫描本地链接；若发现冻结材料失效链接，记录问题并通过新导航补足，不修改保护文件。具体扫描数量、失效项和执行记录见 [documentation_checks.json](../acceptance/stage_g1/documentation_checks.json)。

全量原 tracked 文件、13 个原未跟踪文件、Stage E 292 个冻结文件、F2 基线 47 个受保护产物以及 F3.1/F3.1-E 60 个现有产物均建立独立哈希核对。本轮验收仅为工程整理与文档一致性验收，不更新检索或研究质量指标。
