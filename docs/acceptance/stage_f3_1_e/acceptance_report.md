# Stage F3.1-E：七条失败样本真实对照验收

日期：2026-10-09。工程验收 **PASS**；质量结论 **MIXED / CONDITIONAL**。

## 结论

Coverage Worker 改善了范围边界和若干必要事实，但不是全面改善：严格口径4/7可接受，5题有改善（第2仅部分改善），2题存在混合退化。独立必要事实完整需求9/14→11/14，自报结构完整13/14。出现1个Worker输出协议执行失败，且另一题丢失原基线已有的训练损失。模型调用+18.82%、工具调用+11.54%、七题耗时总和+71.83%。

建议保留显式实验版本供Review，当前证据不足以直接推广到默认或全90题。复核重点是覆盖动作结构/可操作反馈、covered的实际语义，以及复杂阶段任务的成本；本轮没有修改它们或启动F3.2。

这7条是已知失败诊断集、每题一条正式运行，不能代表未知数据泛化能力。评分由AI辅助、非盲评、人工Gold评审为0，不是独立人工Benchmark。

## 实验身份与配置

- Baseline：只读取F2已保存的七条原执行、预测和评分，原七条false标签全部保留；旧Worker未重跑。
- Treatment：初始及恢复工厂显式构造CoverageAwarePaperWorker，原Reviewer、Recovery、Synthesis、Citation Validator正常复用；没有monkey patch或冻结入口替换。
- F3.1基线提交：a38715b8eaa461daefce1f6713d673cf3828e73d。本实验相关新增代码与文档在同一feature/stage-f3-1-worker-coverage分支本地提交。
- 原问题、两论文集合、维度、计划身份、预算、Qwen3-Embedding-0.6B CPU与既有research-stage-e-text集合保持一致，46条文本记录；不重建索引、不改排名算法或语料。
- 使用原deepseek-flash LLM/VLM配置；VLM实际调用0，Planner重放既有固定计划、调用0。
- Worker预算每篇20次模型、8次工具，协议纠正2；operation timeout60秒、原transient retry2。实验额外watchdog420秒，七题均未触发。
- 冒烟通过后正式配置代码SHA固定，七题均一致。正式101次项目Provider尝试，无连接故障；其中1次模型工具参数JSON解析失败。

## 冒烟与记录层修复

先完成一次工程冒烟，7次模型、2次工具。入口Fake回归发现日志分类遗漏最终Reviewer连接故障，修复的是独立记录层，不是研究逻辑/提示词/模型参数，也没有用第一题语义得分调优。原冒烟完整保存在engineering_smoke_attempts/attempt_01，修复原因及前后指纹在repair_record.json。

随后重新开始统一正式版本：首题9次模型、2次工具并完成全链路，固定配置后执行其余六条。下表只用这7条正式结果。工程冒烟额外7次调用不混入正式比较；本轮项目API尝试总计108。

## 逐题对照

| 题目 | 原F2严格标签 → 实验组 | 主要交付变化 | 模型调用旧→新 | 工具调用旧→新 |
|---|---|---|---|---|
| 视觉编码器 | false → true | 保留正确骨干及特征输出；移除性能漂移与无依据整篇否定。 | 15 → 9 | 4 → 2 |
| 视觉语言接口 | false → false | 范围/本地支持改善，但BLIP-2仍缺FC投影及prepended soft prompts，不能算完整成功。 | 11 → 11 | 2 → 3 |
| 冻结范围：BLIP-2 / Flamingo | false → false | Flamingo补齐跨注意力；BLIP-2多次结构提交失败后JSONDecodeError终止，丢失本方原可交付事实。 | 9 → 21 | 3 → 6 |
| 训练目标 | false → false | 删除漂移，但BLIP-2遗漏原基线已有LM loss / prefix LM loss；Flamingo新增未明确支持的text encoder更新推断。 | 16 → 11 | 9 → 5 |
| 语言模型：BLIP-2 / Flamingo | false → true | 恢复BLIP-2的OPT / FlanT5；Flamingo限定于原文有依据的generic autoregressive frozen LM。 | 11 → 12 | 2 → 5 |
| 冻结范围：BLIP-2 / LLaVA | false → true | 恢复BLIP-2两阶段和LLaVA Stage1的W-only、Stage2的W+LLM；调用成本明显增加。 | 11 → 29 | 2 → 5 |
| 语言模型：BLIP-2 / LLaVA | false → true | 保留OPT / FlanT5与LLaMA，去除同伴论文信息缺失的全文否定。 | 12 → 8 | 4 → 3 |

完整case_id、问题、独立评分、覆盖变化、新旧Worker/Reviewer/报告状态及耗时见comparison_results.csv与comparison_results.json。第2题的“改善”是部分改善，严格标签仍false；第3/4题虽然也有局部改进，但主要交付损失仍记为退化。

## 质量与执行指标

| 指标 | Baseline | Treatment | 性质 |
|---|---|---|---|
| 原标签/本轮严格可接受 | 0/7 | 4/7 | 原标签不改；新结果AI语义判定 |
| 完整核心事实交付（敏感性） | 3/7 | 4/7 | 附加风险单列，不放行原违规 |
| 独立必要事实完整需求 | 9/14 | 11/14 | AI按实际引用/必要事实核验 |
| 自报结构完整需求 | 未记录 | 13/14 | 确定性汇总，不能当语义完整 |
| 过早supported需求 | 5 | 2 | AI判定；新2题是接口/训练损失 |
| 证据支持边界风险命题组 | 5 | 1 | 不等于已证伪事实或幻觉率 |
| 无关但有来源性能数字组 | 5 | 0 | 8.7%/54×每题合计一组，不称幻觉 |
| 独立有效核心比较交付 | 3/7 | 4/7 | 不由covered/passed自动推定 |
| 主动证据不足拒答 | 0 | 0 | 无合理拒答样本，拒答正确率分母0/null |
| 新执行失败需求 | 0 | 1 | 模型输出JSON协议失败，非合理拒答 |
| 最终报告completed | 7/7 | 7/7 | 包含第3题带execution_failed缺失单元的报告 |
| Reviewer passed | 14/14 | 13/14 | 剩余1项不可审核，不等于语义正确率 |
| 真实定向Recovery尝试 | 0 | 0 | 判定正常运行，均跳过，无真实恢复成功证据 |

支持边界细分：同伴事实无本地引文1→0；局部检索推出整篇否定3→0；缺乏穷尽依据的only训练范围1→0；新产生优化text encoder的未验证推断0→1。新推断的引文只明确BERT输出参与相似度，不足以确认参数更新，但也不能证明BERT事实上没有更新。结构性引用违规0，不是“语义引用正确率100%”。

第3题旧争议不能靠FC用途推断FC训练状态；第5题Flamingo的model_family=covered实际是泛型autoregressive描述，不是具体家族识别。原参考允许这类有边界的模型描述，不凭模型记忆强求新名称。结构属性语义准确与原问题能否有效回答要分开。

第1/3/4/7题的严格与core口径逐题保存在semantic_assessments.jsonl、independent_ai_review.json；原F2评分原样嵌入。core口径只看完整核心事实并单列附加风险，不编辑输出或改原标签。第4题新正向断言准确性只能部分确认，独立复核把six factual_accuracy从2修订为1；旧复核逐字备份及修订理由保留，严格判断仍false、总体4/7不变。

## 成本

| 成本 | Baseline | Treatment | 变化 |
|---|---|---|---|
| 模型/项目Provider调用 | 85 | 101 | +16 / +18.82% |
| 工具调用 | 26 | 29 | +3 / +11.54% |
| 七题墙钟耗时之和 | 93.53秒 | 160.71秒 | +67.18秒 / +71.83% |

这里的模型次数包括Worker、Reviewer、Synthesis及失败尝试，不是token账单或美元成本。跨论文Worker有并发，七题墙钟求和不是全部模型时延之和。工程冒烟额外7模型/2工具独立记录，不计入正式比较。

计时边界存在差异：F2在runtime setup前开始并在最终drain/close前记录；Treatment在部分setup后开始、在drain/close和诊断收集后记录。加上跨日期服务负载/随机性，不能把71.83%精确归因于覆盖算法。调用数量及具体失败路径可直接核验。Provider别名底层权重版本和F2的缓存revision/endpoint未完整归档，也无法完全控制这些潜在混杂；当前配置模型与既有索引已验证一致。

## 失败链路与已确认原因

第3题BLIP-2的六次覆盖提交（Provider attempt4–9）在顶层字段与逐阶段字段之间反复调整：有的只提交training_stages，另一些把stage1_frozen_modules等塞到顶层，均不满足协议要求的training_stages/frozen_modules/updated_modules精确集合。逐阶段事实本身不能替代协议要求的顶层摘要，校验因此反复ValueError。确认这属于结构协议阻碍，不是没有检索到证据。

最后attempt10返回非法工具参数JSON，由冻结Provider解析器抛JSONDecodeError，原Adapter判permanent，未进行HTTP重试，Worker结束execution_failed；Reviewer正确跳过该需求，Recovery不符合semantic-rejection条件而跳过，Synthesis保留缺失状态而未补写BLIP-2事实。

protocol_diagnosis.json保留每次实际属性集合、具体缺失/多余字段及最终错误。复杂嵌套覆盖与泛化错误反馈是否导致最后非法JSON仅是待验证假设，原非法文本未归档，不能确定其具体语法片段或内部原因。没有静默重跑第3题来挑选更好结果。

另外两个语义缺口属于已确认：第2题language_interface=covered却没有FC/soft prompts；第4题losses=covered却没有LM-vs-prefix-LM损失。元数据通过与Reviewer passed都不能弥补这种需求缺失。

## 工程验收与产物

- 本轮完整工程回归：1043 passed，0 failed；新增88项（运行库10、CLI19、统计56、评分来源3），1个既有Starlette/httpx弃用提示。
- Ruff及原Python目录范围formatter通过；冻结292文件及47份原产物哈希全部匹配，逻辑索引向量/文本/metadata指纹前后一致。
- 7题请求身份与原计划相同、14个实际Worker实例均为CoverageAwarePaperWorker、全部在原预算内；没有替换冻结默认入口、没有新语料/模型/检索算法。
- 真实恢复判定/审核/综合正常接线，13项通过审核、1项执行失败不可审核；Recovery真实尝试0。Fake集成覆盖拒绝→恢复→重审，但它只证明工程连线，不能冒充本轮真实恢复效果。
- 正式配置和执行原记录不变；后处理修正明确区分dependency包装、JSON协议失败和连接失败，不把第3题排除出质量分母。
- 原F2 90条记录、预测、评分、AI参考/候选及七题原诊断没有修改。起始13份未提交旧诊断文件保留、不覆盖。

核心结果在artifacts/quality-f3-1-comparison，验收与可复核镜像在docs/acceptance/stage_f3_1_e。所有真实查询、完整Observation及分数、Worker覆盖/动作、Reviewer状态、Recovery判定/历史、Synthesis输入/最终报告、公开Provider动作/错误/时延均保存；不保存隐藏Chain-of-Thought。

独立脚本：run_worker_coverage_comparison.py先首题、--remaining复用已固定配置继续六题，已有保存case不会重跑；summarize_worker_coverage_comparison.py只读独立评分与原结果，不调用Provider。索引/凭据/基线缺失时前检失败，不能自动替换模型或重建索引。

## 停止位置

本轮仅完成F3.1-E真实诊断实验、统计和验收。保留显式版本供人工Review；默认推广条件尚不充分。没有启动F3.2 Reviewer优化、没有重跑全90题、没有remote push/pull，也没有创建新Tag。
