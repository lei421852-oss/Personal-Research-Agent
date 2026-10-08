# F1 样本原文复核报告

日期：2026-10-08。执行者：Codex AI assistant。方法：AI 阅读固定版本原 PDF 的相应区域并逐条核对候选；不是独立人工审核，也不是运行项目 Reviewer 得出的模型质量评分。

## 完成情况

| 项目 | 数量 |
| --- | ---: |
| 已完成 AI 复核 | 90 / 90 |
| 保留原样本 | 87 |
| 已修订表述 | 3 |
| 仍待本轮处理的问题 | 0 |
| 预期标签改动 | 0 |
| 原 PDF 引用来源 | 32 个 Evidence ID |
| 原 PDF 页 | 5 篇固定版本论文的 13 个不同页 |
| 实际人工审核 | 0 |
| 正式 approved_gold | 0 |

构成仍为事实核验30、Reviewer30、比较15、边界15。Reviewer 保持15个passed/15个rejected；边界保持13个拒答/2个可回答控制。

## 实际核验内容

1. 对32个引用来源核对原始PDF SHA256，并从指定页与region重新抽取文字。32个引用都可在对应区域找到；图注只抽取caption区域，不用图内像素或隐含公式编码补答案。
2. 按30个单论文事实核对训练目标、数值、模型版本、组件和训练阶段范围。保留CLIP的4倍效率结果的实验限定、LLaVA固定v1配置，以及Flamingo模型家族信息不足的边界。
3. 逐条核对30个Reviewer待审论断。正例须完整被给定原文支持；受控负例的改动须与原文矛盾，不因其他子句正确而通过整条。
4. 逐篇核对15条比较中的独立事实与引用归属，不以常识补全Flamingo的具体LM家族，不将机制差异升级为性能优越。
5. 核对15条边界题的访问范围：私人manifest在Prepared Corpus中缺少所求精确值；指定未准备页不可临时读取；caption-only不能回答精确RGB；两个正向控制题有直接可回答证据。

PDF多栏布局会让整页排序文本交错，因此来源定位以指定区域为准。引句匹配对Unicode、连字、空白等进行规范化；这种文字定位只是来源核验的一部分，语义结论由本轮AI阅读后记录，不能当成自动语义蕴含证明。

## 三处修订

### 1. CLIP反例的退化批次边界

`rqv1-reviewer-clip-batch-pairings-rejected`

原论断为“All N squared cross-batch image-text pairings are real positive pairs.”。当N=1时该说法有退化成立的情形，原反例没有写明批次条件。

修订为“For a batch with N > 1, all N squared cross-batch image-text pairings are real positive pairs.”。第4页的N个真实配对和N²−N个错误配对构成明确反证。预期rejected标签保持不变。

### 2–3. BLIP-2两阶段冻结范围

- `rqv1-comparison-blip2-flamingo-frozen-trainable-scope`
- `rqv1-comparison-blip2-llava-frozen-trainable-scope`

原逐论文事实把“两阶段＋冻结图像编码器＋冻结LLM”压缩到一句，容易误解两个阶段都同时涉及LLM。

已按第2页原文展开为：Stage1的视觉语言表示学习使用冻结图像编码器，Stage2的视觉到语言生成学习使用冻结LLM。同步修订逐论文事实、参考答案及预期差异说明；引用、事实组、任务数量与标签保持不变。

## 文件与版本

- 原始F1候选：`data/benchmark/research_quality_v1/`，完整保留。
- AI复核修订副本：`data/benchmark/research_quality_v1_ai_reviewed/`。
- 逐条记录：本目录`case_reviews.jsonl`，包含复核方法、时间、每个Case前后SHA256、核验的Evidence ID、中文理由及修订内容。
- 可直接查看的表格：`review_table.csv`，UTF-8 BOM，包含问题、参考答案、标签、页码、理由和修订原因。
- 原PDF来源重提取结果：`original_pdf_source_checks.json`。
- 汇总：`review_summary.json`；结构/来源校验：`reviewed_dataset_cli.json`；本轮检查命令、耗时及退出码：`check_results.json`。

副本可用现有CLI的`--dataset data/benchmark/research_quality_v1_ai_reviewed`参数加载，无需修改生产逻辑。数据清单已重新计算四份JSONL的SHA256。

## 审核身份与正式Gold

本轮把逐条查证工作完成并登记为`ai_original_pdf_source_review`。没有冒充人工审核者，没有向`human_reviews`填写本轮AI记录，也没有把候选批量标成`approved_gold`。

这是沿用既定F1协议中“正式Gold须实际人工核验原PDF”的字段含义。AI复核副本的样本仍为pending_review/unassigned；正式评分入口保持空Gold分母。`ai_verified`表示本轮AI复核结论，不能对外称为90条独立人工Gold。

34个事实/边界组及关联关系保持不变。按已登记事实组计算有22个关联组件，其中最大组件30条。这个结果用于说明后续划分需要按关联组处理，本轮没有擅自分配dev/test或进行模型优化。

## 工程验证与保留范围

修订副本的90条来源/结构检查通过，现有评分入口确认没有误纳入Gold。本轮完整测试829项通过；Ruff及Formatter通过，具体日志在本目录。既有Starlette/httpx弃用警告仍为1个，没有生产逻辑修改。

原F1基线提交为`49a4ee5a3e70d2ef086e9c5d784649faee87558e`。原F1全部跟踪文件、Stage E冻结文件与历史验收证据保持不变。本轮只增加AI复核副本和审核产物，无项目Provider API调用，无远程Git操作，无F2执行。
