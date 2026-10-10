# 主 Agent 单动作协议修复验收

开始日期：2026-10-10；完成日期：2026-10-11。基线：`c778fb8d808e8f0362dedbdf20527796a5794b7f`。

## 原因与证据边界

用户报告的 `Model must request exactly one tool or finish action.` 对应冻结 `BoundedToolAgent` 的动作数量校验。新主入口原先直接转发模型响应，遇到零动作或多个动作就终止，没有接入有限策略自纠正。Provider 已传入 `parallel_tool_calls=False`，因此仅保留该参数不能替代 Controller 的协议校验。

用户随后提供了完整原问题，但未提供旧任务 ID；原任务内存轨迹仍未导出。对相同问题进行的一次真实复查确认首轮返回了两个动作，说明本次能真实复现并纠正多动作问题；不能据此声称已经提取了旧任务轨迹。本次修复覆盖这两种动作数量违规；没有截取第一个动作，没有放宽 Controller。

## 修改

- `app/research/react_policy.py`：在主入口策略适配层加入结构化协议反馈；最多允许 2 次重新决策。每次请求仅接受一个动作，拒绝批次不执行工具，也不转发响应正文或隐藏推理。
- `app/research/react_runtime.py`：接入实时违规/恢复事件、纠正数量、实际主决策次数及耗尽时的明确失败信息。
- 模型纠正受共享物理调用预算限制，并与正常决策共用最多 7 次主决策上限；工具上限仍由原 Controller 和共享预算执行。Provider transient retry 与策略纠正保持区分。
- 纠正耗尽时原始违规响应交给冻结 Controller 安全终止，不制造 finish 或答案。
- 冻结循环、Worker、Reviewer、Recovery、Synthesis、Retriever、Corpus、Embedding 配置和 UI 源码未修改。

## 验证

- 全量 pytest：1265 passed，0 failed；1 项既有 Starlette/httpx 弃用警告。日志：[test_results.txt](test_results.txt)。
- Ruff check：PASS；Python 源码格式检查：266 files already formatted。日志：[lint_results.txt](lint_results.txt)、[format_results.txt](format_results.txt)。
- 冻结完整性：292 个冻结文件与 47 个受保护产物全部一致，见 [integrity_results.json](integrity_results.json)。
- 新增 7 个确定性测试：零/多动作恢复、耗尽、决策预算、禁用纠正、实际主入口纠正后的唯一动作执行。现有批次拒绝测试补充 3 次模型决策、2 次纠正、0 次工具执行断言。
- 实时事件和主入口 `protocol_trace` 分别记录 `rejected` 与 `recovered`；没有记录模型隐藏推理。

## 一次真实 CLIP 冒烟

问题：`What training objective does CLIP use to align images and text?`

使用现有 Provider、Prepared Corpus、索引和 Embedding 配置，未重建索引。调用正常研究运行时，未手动指定论文范围，模型自主提交 `paper_ids=["clip-2103.00020v1"]`。

| 项目 | 实测 |
|---|---|
| 总状态 / 主 Agent 状态 | completed / completed |
| 主路径 | search_text → finish |
| 实际领域工具 | 1 |
| 主模型决策 | 2 |
| 全部模型调用（含中文展示） | 3 |
| VLM / 多 Agent 研究调用 | 0 / 0 |
| 协议纠正 | 0 |
| 总耗时 | 15,394.21 ms |
| 真实引用 | CLIP 的 3 条 TextEvidence，页码 2、4 |

原始脱敏结果：[real_smoke.json](real_smoke.json)。回答解释了匹配图文的对比目标与对称交叉熵。

这次 CLIP 真实模型运行没有触发协议纠正。随后原问题复查真实触发并完成了纠正，见下节。未执行大规模评测，也未宣称所有问题均可成功。

## 使用与限制

当前 8000 端口上的既有服务在修复前启动，未自动重载 Python 源码。保留该服务以避免清除尚未导出的进程内任务历史；应用新代码需要在原启动终端停止服务，再运行 `.venv/bin/python -m scripts.launch`。浏览器刷新本身不会加载新的后端代码。

默认最多 2 次策略纠正不是成功保证。持续违规仍安全终止，并记录实际动作数量与预算。下一步如需复查原任务，应使用其原问题与任务 ID，不更换模型或语料。

仅进行本地 Git 操作；保留原有未跟踪诊断文件，不执行远程推送。

## 用户原问题的一次真实复查

问题：比较 BLIP-2 与 Flamingo 的视觉语言连接机制，说明 Q-Former、Perceiver Resampler 与门控交叉注意力如何将视觉特征传递给语言模型，并引用当前论文库中的证据。

使用原模型、语料、索引、默认 Worker 配置；未预写答案、未传入硬编码论文范围。实际路径：

```text
中文问题适配
→ 主模型提出 2 个动作
→ rejected（0 个工具执行）
→ 协议观察反馈
→ 模型自行选择 1 个合法动作
→ search_text(BLIP-2)：5 条证据
→ search_text(Flamingo)：6 条证据
→ finish
→ 中文展示
```

- 总状态及主 Agent 状态：completed；answer_status：answered。
- 主决策 4 次，其中协议纠正 1 次；实际工具执行 2 次。
- 全部模型调用 6 次：问题适配 1 + 主决策 4 + 中文展示 1。
- VLM 调用 0，多 Agent 研究调用 0；耗时 28,950.22 ms。
- 结果包含 Q-Former 投影为软视觉提示、Perceiver 重采样及门控交叉注意力的比较，附真实检索引用。
- 主 Agent 判定已有文本证据足够，直接答复，未调用可选多 Agent 研究工具。因此没有执行 Planner / Worker / Reviewer / Recovery / Synthesis；不能把该结果描述成已经审核的多 Agent 研究报告。
- 引用身份可追溯不等于自然语言比较结论已经经过独立人工核验。

脱敏原始结果：[user_question_smoke.json](user_question_smoke.json)。实际模型恢复了单动作协议；被拒绝批次没有执行，新增调用成本在模型指标中保留。
