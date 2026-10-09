# G2.1 协议失败定位

依据：G2唯一正式执行及起点`48b33f3880da769c4eedfb5a96a7b1fcd7c7360a`源码。不读取Gold或参考答案调试。

## 已确认事实

- G2有4次multiple_actions：BLIP-2一次；Flamingo在Steps 1/5/10返回3/4/2个动作。前两次允许纠正，第三次终止；`max_protocol_corrections=2`没有失效，拒绝动作未执行。
- 8次search_text参数失败均包含`paper_id` extra_forbidden（BLIP-2三次、Flamingo五次）。[SearchTextInput](../../../app/tools/paper.py)要求`paper_ids`列表；ReadPageInput /FindFigureInput使用单数`paper_id`。
- [Worker Controller](../../../app/research/worker.py)先用input_model严格校验，再要求search_text的paper_ids等于单元素本论文ID。工具参数不被自动改名；Scope不被扩大。
- Worker系统指令已有Exactly ONE；Provider已发送parallel_tool_calls=False，并原样返回所有规范化tool_calls。Controller没有选取第一个。因此不是缺少API关闭并行参数，也不能从单次轨迹证明某个Provider内部实现原因。
- 多动作反馈只有计数、Exactly One与通用恢复指令；参数反馈有字段与Pydantic错误类型，但没有合法结构示例。
- 参数异常的Observation名称是worker_controller，而Trace才记实际search_text；原Decision Context没有附该动作名称。新Fake测试证实，仅按Observation名称无法定位工具契约。新增适配从同一轮公开Trace绑定被拒工具，不根据字段名猜工具或选择下一动作。
- 共用search_text描述要求跨论文比较包含全部named papers；单论文Worker系统指令要求本论文唯一scope。该描述用于通用Agent是合法的，但在paper-local Worker上下文中存在相互竞争的指令。
- `resolve_requirements`合法地在一个调用中批量提交多个resolution。它与一次响应返回多个function call不同，原指令没有详细说明这两个“批量”的区别。

## 推测与未验证原因

字段单复数、上下文顶层paper_id、共用跨论文说明和批量resolution可能增加模型协议混淆；G2可确认输出违规，不能独立证明这些因素各自的因果贡献。模型多动作行为也可能受Provider和模型随机性影响，本阶段只做一次真实验证。

## 冻结约束与最小修复边界

worker.py、paper.py、Provider及原研究入口均在Stage E冻结清单。它们保持原哈希。新增[WorkerProtocolAdapter](../../../app/research/worker_protocol.py)组合原AsyncLLMAdapter，发送模型请求副本：

1. 简短澄清一个action是一个function call；批量resolution只能在一个call内部。
2. 依据当前Schema和Controller约束，给模型所见search_text声明单元素paper_ids、required和合法示例；覆盖共用跨论文说明。实际Tool input_model不变。
3. 将现有multiple_actions /invalid_arguments Observation派生为可操作协议反馈，提供计数、字段错误、允许字段和例子。没有论文事实、Gold或新检索结果。
4. 模型返回动作原样交给冻结Controller；没有截断、参数别名兼容、自动scope修正、自动执行或自动LLM重试。

新版独立验证入口显式装配原`PaperResearchWorker`加协议适配，不使用Coverage Worker。Reviewer、Recovery、Synthesis原类及语义规则不变。冻结旧入口仍可重放历史基线；本次不是静默替换其导入。纠正、模型/工具预算、transient retry、超时、资源槽和取消均由原实现管理。
