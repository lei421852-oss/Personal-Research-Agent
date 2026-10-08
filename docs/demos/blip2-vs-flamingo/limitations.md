# 本次演示的限制与未完成部分

## 结果边界

这是一条唯一正式执行的真实工程案例，G2判定 **CONDITIONAL**：研究链路与证据展示可复核，但未实现有效的BLIP-2 /Flamingo比较。报告结构 completed 指Synthesis按合法矩阵完成组装；它不等于12项研究需求均有已审核证据。

| 论文 | supported /已审核 | insufficient_evidence | execution_failed |
| --- | --- | --- | --- |
| BLIP-2 | 5 | 1（cross_attention_gating） | 0 |
| Flamingo | 0 | 0 | 6 |

Flamingo协议纠正耗尽与证据缺失不同。它已检索到5条文本并通过read_page扩展到8条已观察证据，但没有提交合法需求结果；因此证据不能直接跳过Worker/Reviewer进入报告。没有Flamingo的VerifiedClaim，不允许制造跨论文比较，所以比较数为0。

Reviewer对5个BLIP-2 Claim均passed，另外7个单元not_reviewable（6个执行失败、1个证据不足）。实际审核分母为5；100% ReviewPassRate不等于100%任务成功或事实准确率。Recovery有12个跳过结果，无eligible语义拒绝、无恢复Worker；不能用它修复任意执行失败。

## 语义与证据边界

- Claim是模型生成内容，Reviewer passed仍是模型判断。独立人工语义核验：0次；不标记成human-approved。
- 程序核验了引用ID、论文/维度、观察账本、报告与SynthesisInput的一致性，并逐字对比引用文本。结构校验不能证明所有技术解释都被证据蕴含。
- BLIP-2关于“interaction granularity”“only”等措辞含解释或较强断言，保持原输出，不因展示需要改写成更可信的文字；其语义范围仍须人工核查。
- 研究仅使用Prepared文本区域和图注；本次没有analyze_figure，VLM调用0。不声称进行了像素级视觉分析或完整PDF阅读。
- BLIP-2的cross_attention_gating不足不能解释成整篇论文没有相应机制；只代表本次Worker没有获得它认为足以提交该需求的证据。
- 不用Paper Profile或模型记忆补全Flamingo的Perceiver Resampler /Gated Cross-Attention事实，也不以本次缺口判断论文自身缺少这些设计。

## 成本、复现与计数

31次LLM尝试包含1次真实Planner、24次Worker、5次Reviewer和1次Synthesis。基础设施retry为0；4次多动作拒绝及其他参数/重复动作反馈由实际模型决策消耗预算，不能称为HTTP重试。模型协议是Exactly One Action，拒绝的多动作不计为实际工具执行。

9次工具为6次search_text、3次read_page。正式执行还进行一次Embedding可用性检查，所以正式命令的Embedding边界调用为7；另一次独立预检多1次。没有记录SDK token usage或金额，不根据调用数伪造货币成本。

封装总计41.96秒，包含正式预检及本地模型准备；Planner与研究合计35.66秒，内层默认研究链31.30秒。并发Worker耗时不能简单相加作为总墙钟耗时。超时设420秒，实际未触发；同步线程清理不能被协程取消强杀。

同一输入再次调用动态模型alias可能产生不同维度、动作或结果。复现需要现有匹配索引、模型缓存和Provider配置；本入口不自动修复缺失条件，不承诺从空环境一键完成。新环境应先按G1准备依赖与索引，再执行预检。本轮没有更换模型、重建索引或重复真实运行。

## 本阶段未做的事情

没有优化Prompt、Worker、Reviewer或固定评测集；没有启用Coverage Worker；没有为Flamingo失败重跑或补写答案。下一步若要处理此失败，需要单独授权质量/协议任务，本次仅交付可复核失败记录并停止。
