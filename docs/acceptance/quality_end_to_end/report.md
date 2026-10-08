# 研究质量评测：端到端完成报告

日期：2026-10-08。状态：**整套流程已完成**。90条固定样本全部真实执行，预测文件、逐题执行记录、AI语义核对、总体/dev/test评分和失败清单均已生成。

## 你现在只需要看什么

先看本报告；想查看具体问题时打开`case_results.csv`。无需再创建占位的`predictions.jsonl`或`human-assessments.jsonl`。

实际输出统一在`artifacts/quality-end-to-end/`，交付副本在本对话D盘产物目录。文件包括：

- `predictions.jsonl`：90条真实预测；执行失败保留空预测字段，不编造答案。
- `executions.jsonl`：真实执行记录、公开工具动作、需求/审核/恢复/综合结果和调用计数。
- `ai-assessments.jsonl`：42条有效开放答案的AI语义核对，绑定Case与Prediction哈希。
- `quality-score-all.json / quality-score-dev.json / quality-score-test.json`：三份评分结果。
- `evaluation_summary.json`：真实执行、评分、成本及失败的统一口径。
- `case_results.csv`：90条逐项结果、实际答案、审核理由和错误原因。

审核来源明确为AI。没有写入虚假的人工评分文件，人工评分数为0。

## 实际运行与结果

已先执行四类各一条dev试跑，再在同一配置下完成其余86条。试跑结果保留，不重复调用，也没有依据试跑结果调整生产Prompt、标签或检索。模型使用当前配置的`deepseek-flash`，文本检索复用Qwen3-Embedding-0.6B与固定Stage E研究索引。

| 范围 | 通过 / 分母 | AI辅助质量判定比例 |
| --- | ---: | ---: |
| 全部 | 79 / 90 | 87.78% |
| dev | 17 / 18 | 94.44% |
| test | 62 / 72 | 86.11% |

| 任务 | 通过 / 分母 | 判定来源 |
| --- | ---: | --- |
| 单论文事实 | 26 / 30 | 27个有效答案由AI核对，其中1条不通过；3条连接故障计失败 |
| Reviewer | 30 / 30 | 实际passed/rejected与已复核参考标签比较 |
| 跨论文比较 | 8 / 15 | AI核对完整性、引用支持和研究范围 |
| 证据不足/边界 | 15 / 15 | 实际结构化拒答决策与参考标签比较 |

共87条有效输出、3条执行失败。有效输出中8条未通过本轮严格语义/范围核对，因此79条质量通过。所有90条都进入总体分母，没有把失败题从结果中删除，也没有将API故障算成正确拒答。

Reviewer混淆矩阵：TP=15、TN=15、FP=0、FN=0；本组有效标签分母30，错误接受/拒绝率均0。该组是15组来源事实与受控矛盾，不代表生产Reviewer对所有复杂研究主张都达到100%正确。

比较四轴总分为100/120（83.33%）；逐轴比例为事实准确26/30、差异正确26/30、证据支持25/30、范围完整23/30。四轴平均不自动决定整体通过；整体比较任务为8/15。技术要点部分正确的答案可能因为未完成所问维度或引用不覆盖全部断言而不通过。

## 发现了哪些真实问题

### 连接故障：3条

`rqv1-claim-clip-reported-efficiency`在Reviewer阶段出现APIConnectionError/RemoteProtocolError，两次重试后仍失败；`rqv1-claim-blip-vision-encoder`与`rqv1-claim-blip-med-functionalities`在Worker阶段出现同类故障。各阶段均保留实际调用记录和retry_exhausted信息。

这三条没有有效答案，按执行失败计分。本轮没有额外重跑它们来替换第一轮结果。

### 引用身份有效仍可能支持不完整

Flamingo冻结组件事实题的主张已被生产Reviewer通过，但其实际引用只覆盖冻结骨干和Resampler，却加入gated cross-attention dense layers细节。对应第二连接组件/门控细节没有被实际引用覆盖，因此独立AI核对判为不通过。

这不是虚构Evidence ID；它说明**引用身份校验与语义支持是不同层次**，生产Reviewer通过也不能直接当成质量Gold。

### 比较题的范围漂移与遗漏

BLIP-2/Flamingo的多条比较加入8.7%/54×性能数字，却没有充分完成当前接口、训练范围或语言模型家族的比较。部分答案还声称整篇论文不记载其他模型信息，有限prepared片段不能证明整篇缺失。

BLIP-2/LLaVA冻结范围比较只聚焦生成/Stage2，遗漏参考要求的第一阶段区别。BLIP-2/LLaVA语言模型比较的核心家族事实正确，但附带的整篇缺失断言没有完整来源支持。

所有不通过条目的具体理由在逐条CSV与AI核对JSONL中，方便之后复查评分判断。

## 调用与耗时

- 观察到的Provider物理尝试：456次，包含基础设施重试；不是token账单。
- 研究工具调用：107次。
- VLM工具尝试：0次。caption-only题没有像素访问或视觉工具。
- 定向恢复需求：1次，使用冻结的最多一轮Recovery逻辑。
- 各案例实际耗时之和：863.998秒，约14.40分钟。

案例耗时不包含两次进程的Embedding初始化、批次之间工作、适配代码开发、逐条AI评分、测试与归档，因此不能把14.40分钟说成整个协作任务的总耗时。慢案例包括连接重试耗时和多Worker/Reviewer/Synthesis调用。

评分脚本本身不调用Provider，原始评分JSON中的`real_provider_calls=0`描述评分步骤；真实执行证据在executions/run_manifest中，统一摘要记录456次观察到的尝试。

## 评测范围与限制

事实/比较及常规边界题复用Worker→Reviewer→Recovery→Synthesis；Reviewer题直接测试给定论断/证据审核；caption-only题使用没有研究工具的受限结构化回答。它们是一套模块质量评测，不能当作同一Agent在三种系统间的公平对照实验。

目标论文和维度由Case提供，生成固定评测计划；本轮不评估Planner的开放论文选择。没有运行Text-only RAG/Fixed Multimodal RAG/Agent三系统比较，也没有评估图像像素理解。

参考集是AI原文审核数据，开放输出由Codex AI辅助评分，非盲法独立人工Gold。评分较严格，要求所问核心要点完整、实质断言由实际引用支持、研究范围正确；某些额外上下文或整篇缺失表述的判定可能与其他审核者不同。逐条理由与原答案保留，便于复核，不能将本轮比例宣传为独立人工认证准确率。

## 工程与冻结验证

全量测试862项通过，Ruff、Formatter与空白检查通过；保留1个既有Starlette/httpx弃用警告。新增执行与AI评分测试12项覆盖输入不泄漏Gold、失败不伪装为拒答、caption无像素访问、伪引用拒绝、AI/人工分离及哈希绑定。

Stage E的292个冻结文件SHA256全部匹配，297个原跟踪文件未改动。论文语料、固定评测标签、生产Planner/Worker/Reviewer/Recovery/Synthesis、Retriever、API/UI均未修改。仅新增评测适配和明确AI评分入口；不调整生产行为来提高本轮成绩。

原Frozen聚合指纹仍为`44c7afd36c793eff86faed077b7c917a5c6a200e3d652bfbf5b19e83cff0362e`。

真实结果与测试证据均已归档；不执行远程Git操作。本轮到此完成，后续若优化，应针对已记录的引用支持、范围和阶段遗漏问题另开明确任务，保留本轮结果作为基线。
