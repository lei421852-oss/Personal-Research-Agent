# Stage G2.1：Worker 协议稳定性修复验收

2026-10-09。**协议工程验收PASS；一次真实Worker协议验证PASS；完整研究主题CONDITIONAL。** 不将合法有界完成等同于证据充分或成功跨论文比较。

## 1. 已确认问题与实现

[diagnosis.md](diagnosis.md)区分确认事实和假设。G2有4次多动作、8次参数失败。已有Exactly One和parallel_tool_calls=False没有防止真实违规；共用跨论文Schema描述与单论文scope竞争，原参数Observation又使用worker_controller通用名字，缺少工具绑定与合法示例。

冻结worker.py、Tool Schema、Provider及研究入口不允许改写。因此新增：

- [worker_protocol.py](../../../app/research/worker_protocol.py)：透明AsyncLLMAdapter组合；克隆请求和Schema，澄清单function-call及批量resolution，单论文paper_ids约束与真实执行要求一致；从最新Observation和同轮公开Trace生成字段错误、允许字段与合法示例。
- [protocol_runtime.py](../../../app/research/protocol_runtime.py)：仅装配原PaperResearchWorker、ResourceGate、Reviewer、Recovery和Synthesis；不复制研究循环、不修改语义规则，不使用Coverage。工具注册顺序与G2保持一致。
- [run_worker_protocol_demo.py](../../../scripts/run_worker_protocol_demo.py)：独立验证入口；只读G2问题与论文身份，不读取Gold；复用G2预检、旁录检索和报告renderer，默认仅预检，不补建索引。

响应完全原样返回；原Controller继续拒绝所有multiple actions与额外参数。没有截断第一个动作、paper_id→paper_ids改名、自动scope选择、自动重试或强制finish。仅最新错误追加反馈，成功之后历史错误不继续施加纠正指令。

原max_protocol_corrections=2只约束多动作纠正机会；invalid_arguments仍消耗原模型预算、不会额外增加该计数。模型20 /工具8、单操作60秒、transient retry2、并发资源槽及取消/drain均保持原值。恢复及综合预算不变。

新入口使用协议增强；旧冻结入口仍按历史方式运行。这是保留冻结约束的版本化装配，不能声称所有旧入口或API已自动采用修复。

## 2. 测试与工程验收

17项新增确定性测试全部通过，覆盖单合法动作、多动作不执行、反馈真实计数及可用示例、严格拒绝paper_id、LLM自行提交paper_ids、部分结果保留、模型/纠正预算耗尽、合法不足、异论文scope拒绝、未观察引用拒绝、取消和超时，以及Reviewer拒绝→一次Recovery→重新审核→Synthesis。

完整工程回归：**1071 passed，0 failed，1既有Starlette/httpx warning，14.77秒**。Ruff全范围通过，232个Python文件已格式化。命令与输出见[checks_before_execution.json](checks_before_execution.json)。未引入新linter或框架。

保存了原分支、HEAD、501个tracked文件、13个原未跟踪文件及10个G2原始运行文件的哈希，见[baseline.json](baseline.json)。本轮完整性结果见[verification.json](verification.json)：Stage E292冻结文件、F2等47登记产物、F3额外60产物、全部原文件及G2原轨迹保持不变。生产索引前后逻辑指纹一致，无重建、排名或Embedding变化。

## 3. 唯一真实验证

实际命令使用仓库.venv/bin/python：

```bash
python -m scripts.run_worker_protocol_demo --output-dir artifacts/stage-g2-1-worker-protocol
python -m scripts.run_worker_protocol_demo --output-dir artifacts/stage-g2-1-worker-protocol --execute
```

第一条仅预检，第二条唯一正式执行。新代码基于提交19a8199与2fa15ea；真实开始前源码SHA见[run_manifest.json](results/run_manifest.json)。问题逐字等于G2，BLIP-2 /Flamingo论文集合、Prepared Corpus、Qwen模型/cpu、deepseek-flash配置、主要预算及索引指纹均相同，没有更换Provider或反复调Prompt。Planner原代码与模型不变，但本次重新生成计划，不硬编码预期答案。

## 4. 协议和完成状态对比

| 指标 | G2 | G2.1 |
| --- | ---: | ---: |
| Worker multiple_actions | 4 | 0 |
| Worker invalid_arguments | 8 | 0 |
| Worker模型决策 | 24 | 12 |
| Worker协议纠正 | 3 | 0 |
| completed Worker | 1 | 2 |
| execution_failed需求 | 6 | 0 |
| 合法resolved需求 | 6/12 | 14/14 |
| 已审核Claim | 5 | 4（BLIP-2三条、Flamingo一条） |
| insufficient_evidence | 1 | 10 |
| 结构合法跨论文比较 | 0 | 0 |

本次真实运行未触发新Worker参数/多动作错误反馈；其恢复路径由Fake tests验证。不能声称真实模型在本次调用中依靠该反馈自我纠错。尚有一次BLIP-2冗余读页反馈，未执行工具，原防重复规则保持。

## 5. 研究限制与新的受控纠正

Planner生成7个维度 /14需求，G2为6/12。新计划存在论文专属维度却按每篇展开，导致另一篇面对不适合自身的组件问题；无同维度双论文VerifiedClaim，所以比较数为0。该结构变化是明确混杂因素，不能用5/12→4/14判定同题质量提升或退化。

Flamingo一次检索后提交1 supported +6 insufficient。是否过早停止、哪些不足能被现有材料填补属于待审查语义问题，不将resolved=100%当作质量成功。不修改Planner、Worker语义完成规则或研究问题修复这些缺口。

Synthesis第一次发生invalid_evidence_reference，原OutputValidator拒绝；一次纠正后completed。综合2次调用、协议纠正1、citation_validation_failures1是被拦截输出计数，最终结构引用违规为0，不等于“虚构Citation1个”。Reviewer四条passed，无语义rejected；Recovery检查后跳过，无恢复Worker。

研究报告及[证据映射](results/evidence-map.md)由实际输出生成，Claim原文未改写。程序核验引用身份、来源、维度、SynthesisInput与报告，不能代替语义蕴含或独立人工事实审核；本轮人工事实审核为0。VLM调用0，不宣称像素级研究能力。

## 6. 成本与耗时

| 边界 | G2 | G2.1 |
| --- | ---: | ---: |
| 全链LLM（含Planner） | 31 | 19 =Planner1 +Worker12 +Reviewer4 +Synthesis2 |
| 实际工具调用 | 9 | 8 =search_text7 +read_page1 |
| VLM /Provider transient retry | 0 /0 | 0 /0 |
| 封装总耗时 | 41.96s | 52.69s |
| Planner及研究耗时 | 35.66s | 46.63s |
| 内层研究 | 31.30s | 40.24s |

LLM下降38.71%，工具下降11.11%，总耗时增加25.58%；不声称全面降低成本。并发、不同调用延迟、增加一次综合纠正及计划变化使耗时不能仅由请求数量推断。没有SDK token usage或货币账单，未虚构金额。本次仅一个样本，不保证长期稳定或一般质量改善。

## 7. 交付与停止

[案例对比](../../demos/blip2-vs-flamingo/g2-1-comparison.md)、[comparison_summary.json](comparison_summary.json)、results内的真实报告、预检、摘要、紧凑执行记录、源码指纹，以及本目录诊断和核验日志构成交付。完整原始execution.json与逐次retrievals在ignored artifacts及用户证据包，不覆盖G2任何文件。

本地分支fix/stage-g2-1-worker-protocol；按修复、测试、真实验收拆分提交，最终记录见[git_delivery.json](git_delivery.json)。原13个未跟踪文件保留，没有远程操作。

协议修复目标通过；完整研究展示仍有计划投影与证据充分性限制。唯一真实验证后已停止，不进行第二轮Prompt或质量优化，不自动进入G3。
