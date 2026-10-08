# F1 样本审核完成与可用参考集验收

日期：2026-10-08。结论：PASS。90条样本已经完成AI原文复核并登记为可用参考，待审核条目为0。

## 交付状态

| 项目 | 结果 |
| --- | --- |
| 数据目录 | `data/benchmark/research_quality_v1_ai_reference/` |
| 样本数 | 90 |
| 审核状态 | 全部 `approved_ai_reference` |
| 来源类型 | `ai_reviewed` |
| 待审核 | 0 |
| dev / test | 18 / 72 |
| 类别 | 事实30、Reviewer30、比较15、边界15 |
| 共享事实/边界组 | 34；跨划分泄漏为0 |
| 实际人工审核 / 人工Gold | 0 / 0，未代填人工身份 |

本轮将之前逐条查证的结果落到样本状态、来源证明、分组划分和评分入口，形成可直接读取的AI审核参考集。审核方法为`ai_original_pdf_source_review`，AI结果不被写成独立人工Gold。

## 审核证明

全部批准都来自已经保存的90条逐项原PDF审核记录。每条保留审核者、时间、中文核验理由、核验Evidence ID、访问范围检查、源Case SHA256、审核记录SHA256，以及当前问题/答案/标签/引用/事实组/划分的内容SHA256。

已有三处表述修订继续保留：两个BLIP-2冻结范围比较按阶段展开，一个CLIP反例补充N>1边界。标签、类别数量、论文语料和证据来源没有改变。

缺少、重复、过期、未通过或原文定位未核验的审核记录不能由登记脚本批准。修改语义内容或split后，旧审核哈希失效。原始候选与AI修订副本保留，不覆盖历史验收文件。

## 分组划分

| 类别 | dev | test |
| --- | ---: | ---: |
| 事实核验 | 5 | 25 |
| Reviewer | 6 | 24 |
| 比较 | 3 | 12 |
| 边界 | 4 | 11 |
| 合计 | 18 | 72 |

按照已有事实组连通组件分配，不按模型成绩选择。dev组件来自视觉编码器比较组、CLIP对称损失/可回答控制组、caption-only边界组和BLIP CapFilt初始化事实组。

边界子类型不是均匀分层：caption-only统一在dev，private-manifest与未准备页统一在test；两侧各有一个可回答控制。这是同类改写组隔离的结果，应随未来结果说明，不能声称所有子类型两侧均覆盖。

## 可用入口

`load_dataset(OUTPUT_DATASET)`可加载全部90条；`approved_references(dataset)`默认选择72条test，`split="dev"`选择18条，`split="all"`选择90条。

CLI继续使用`validate_quality_benchmark`和`score_quality_benchmark`，指定本数据目录即可。评分结果会标记`reference_kind=ai_reviewed`和`result_scope=provisional_ai_reviewed_reference`，分母字段使用reference名称，不伪称人工Gold。

人工Gold原有选择器与要求保留。开放事实/比较的未来预测仍需单独语义评分，参考批准不会自动给未来模型答案打分。当前没有真实预测，入口状态为`awaiting_predictions`；本轮不报告真实模型准确率。

## 实际检查

- 全量pytest：846 passed，0 failed，1个既有Starlette/httpx弃用警告。
- 本轮新增17项回归：AI状态、原审核绑定、改动失效、分组隔离、与人工Gold区分、评分分母命名、缺失预测状态和旧Case哈希兼容。
- Ruff通过；最终Formatter通过，203个文件符合格式。初次格式检查的一处测试排版问题已修正，首次与最终日志均保留。
- 90条来源/结构校验通过：已批准AI参考90、候选0、人工Gold0。
- 实际评分CLI选择全部90条参考；无真实预测时明确等待预测。
- 登记脚本重复执行，JSONL与manifest字节相同。原候选准备脚本仍保持旧版本字节输出。
- Stage E的292个冻结文件哈希匹配，297个原跟踪文件未改动；原F1数据、修订副本和历史验收证据保留。

实际命令、退出码和耗时见本目录`check_results.json`与`cli_checks.json`；结果与冻结核验汇总见`completion.json`。

仅增加/调整独立评测数据与入口，没有修改Planner、Worker、Reviewer、Recovery、Synthesis、Retrieval、Corpus、API或UI。项目Provider API调用0次，没有运行正式Benchmark、远程Git或F2。
