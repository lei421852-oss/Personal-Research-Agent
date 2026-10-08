# Flamingo Worker 失败诊断

本页只读提取唯一正式运行的行为事实，不修改 Worker 或 Prompt。

首次错误：Step 1 的模型响应有3个 actions，Controller拒绝全部并提供一次协议纠正。
终止原因：Step 10 再次出现2个 actions，此前两次纠正机会已使用，返回 `protocol_correction_exhausted`。

| Step | Action | 状态 / 原因 | 动作数 | 纠正可用 |
| --- | --- | --- | --- | --- |
| 1 | worker_policy | multiple_actions | 3 | True |
| 2 | search_text | invalid_arguments | — | — |
| 3 | search_text | invalid_arguments | — | — |
| 4 | search_text | invalid_arguments | — | — |
| 5 | worker_policy | multiple_actions | 4 | True |
| 6 | search_text | invalid_arguments | — | — |
| 7 | search_text | invalid_arguments | — | — |
| 8 | search_text | success | — | — |
| 9 | read_page | success | — | — |
| 10 | worker_policy | multiple_actions | 2 | False |

Steps 2、3、4、6、7 是参数失败：`paper_id` 为 extra_forbidden，工具需要的是 schema 定义的参数。公开 arguments 是已有运行时投影，不包含所有违规字段；错误字段见 failure.validation_errors，不能仅凭投影断言模型没有传过 paper_id。

Steps 8/9 已成功检索 Flamingo 并读取预处理 Page 10，说明不是索引缺少 Flamingo。终止发生在提交合法需求结果之前，所以六个单元均为 execution_failed；不能改称证据不足。

研究 Recovery 只恢复合法语义审核 rejected。执行失败没有 eligible 状态，本轮正确跳过；协议 self-correction 与 Reviewer→Worker Recovery 是不同机制。

BLIP-2也在Step 1返回两个 actions，随后自行纠正，最终提交5 supported +1 insufficient。这说明本次两篇的策略结果不同，不能归结为 Controller 自动选择了动作或统一 Provider 连不上。

所有 multiple-actions 拒绝均未执行被拒工具。实际请求参数错误和协议失败已证实；为什么同一模型连续违反协议、是否能通过其他策略改善均未验证。本阶段不调优、不重跑。
