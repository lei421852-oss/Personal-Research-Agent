# BLIP-2 与 Flamingo：视觉语言连接机制研究演示

本案例使用既有 Prepared Corpus、Embedding 和研究索引，真实调用 Planner，再使用默认 `PaperResearchWorker` → Reviewer → 一轮以内 Recovery → Synthesis。没有启用 Coverage Worker，没有将预写答案或评测 Gold 送入运行时。

**本次结果是部分完成，不是成功的完整跨论文比较。** Planner 生成6个维度 /12个需求；BLIP-2有5个已审核 Claim和1个证据不足，Flamingo因 `protocol_correction_exhausted` 留下6个执行失败。最终报告生成状态为 completed，但证据覆盖为5/12（41.67%），跨论文比较为0。G2整体验收为 **CONDITIONAL**，工程链路验证通过，展示主题未完整达成。

正式运行只有一次：31次LLM实际尝试（Planner1、Worker24、Reviewer5、Synthesis1），9次工具、6次实际文本检索，VLM0次；封装总耗时41.96秒，含Planner的研究耗时35.66秒。Recovery检查执行但没有合格需求，实际恢复轮数为0。详见 [失败诊断](failure-analysis.md)，不能把报告 completed 或 review_pass_rate=100% 当作题目完成率。

## 阅读顺序

1. [研究问题](question.md)：唯一一次正式运行的原始问题。
2. [研究报告](research-report.md)：既有 renderer 对实际 ResearchReport 的直接渲染，未人工改写 Claim。
3. [证据映射](evidence-map.md)：已审核 Claim、Evidence ID、PDF 页码和逐字原始 Prepared Evidence。
4. [执行摘要](execution-summary.json)：实际配置、调用、耗时、状态及索引指纹。
5. [限制与失败](limitations.md)：缺失、拒绝、执行失败和人工尚未核验的范围。
6. [G2 验收报告](../../acceptance/stage_g2/acceptance_report.md)：完整工程核验与交付位置。

## 如何运行

环境安装、六项 Provider 变量和索引区别见 [G1 开发指南](../../guides/development.md)。须从源码仓库根目录执行，并已有 `data/chroma/stage-e` 的 `research-stage-e-text` collection。脚本不会下载新模型或补建索引，缺少条件直接输出 blocked。

先预检，模型调用为零，Embedding 使用当前本地缓存：

```bash
python -m scripts.run_research_demo \
  --config configs/cross_paper_eval.json \
  --env-file .env \
  --output-dir artifacts/my-blip2-flamingo-demo
```

预检为 ready 后执行一次真实任务：

```bash
python -m scripts.run_research_demo \
  --config configs/cross_paper_eval.json \
  --env-file .env \
  --output-dir artifacts/my-blip2-flamingo-demo \
  --timeout 420 --execute
```

默认 question 与本目录 question.md 对应，论文 ID 为 `blip-2-2301.12597v1` 和 `flamingo-2204.14198v1`。可用 `--question` 和 `--paper-ids` 明确指定其他已准备论文与问题；不支持凭元数据候选自动下载全文。参数帮助：

```bash
python -m scripts.run_research_demo --help
```

存在 `execution-started.json` 或 `execution.json` 的目录不允许重跑覆盖。要做另一次独立复现，使用新输出目录，保留每次实际结果，不选择最好一次来替换本案例。LLM 服务的动态 alias 和非确定性可能导致路径、内容和成本不同；可复现指入口、配置、来源与记录可检查，不保证逐字重现模型答案。

## 如何核对执行与证据

- `execution.json` 保存 Planner preparation、初始 Worker / Reviewer、Recovery outcome、SynthesisInput 和最终报告；`retrievals.json` 旁录实际 query、paper scope、Top-K、分数与原始命中。
- `research-report.json` 是实际领域报告；`research-report.md` 必须等于既有 `render_markdown` 的结果。引用白名单、Claim 所属论文与维度、报告矩阵经程序再校验。
- `evidence-map.md` 只展示合法已审核 Claim，不把 rejected / unavailable 的正文变为事实。尚未独立人工事实审核；Reviewer passed 是模型审核结果。
- 原论文：[BLIP-2 PDF](../../../data/research_papers/blip2/paper.pdf)、[Flamingo PDF](../../../data/papers/flamingo/paper.pdf)。来源记录：[BLIP-2](../../../data/research_papers/blip2/prepared_evidence.jsonl)、[Flamingo](../../../data/papers/flamingo/prepared_evidence.jsonl)。PDF 页码从1开始，不等于印刷页码。
- 大体积原始执行在 ignored `artifacts/` 中，完整交付包另行保存；Git 中保留非敏感报告、摘要和来源指纹，不提交缓存、索引或凭据。
- [紧凑执行记录](execution-record.json)保留本次 Planner、初始 Worker、Reviewer、Recovery资格和SynthesisInput，提供原始完整文件SHA256；完整轨迹247237字节，未直接提交。已提交106947字节的选择字段副本没有改写论断或状态。

内部 `run_case` 的 Planner 计数为0，因为它执行调用方传入的计划；本封装另行真实调用 Planner 一次，记录在顶层 `preparation`、`planner_events` 和 `planner_model_calls`。内部继承的历史“重放冻结计划”文案只描述旧评测入口，本演示的真实计划来自本次外层 Planner，不复用评测答案或既有计划。

## 工程边界

默认研究组件与 Prompt 均未修改。同步依赖线程不能被协程超时强杀，420秒控制研究等待，清理 drain 仍可能等待已发请求结束。Provider 仅按既有策略有限重试；协议纠正与研究 Recovery 各自有界。一次完成报告不等于全部需求有证据，也不等于人工事实核验或质量 Benchmark。
