# 工作台历史回放来源与边界

只读既有版本管理记录，不发模型请求、不检索、不重写原报告，也不补造事件时间。

| case_id | 来源 | 读取文件 |
| --- | --- | --- |
| g2 | [原G2演示](blip2-vs-flamingo/README.md) | execution-record.json、research-report.json、execution-summary.json。 |
| g2-1 | [原G2.1验收](../acceptance/stage_g2_1/acceptance_report.md) | results/中同样三个文件。 |

轨迹提供Planner、Worker、Review、Recovery，报告与总成本从同次companion文件读取。G2.1缺顶层请求元数据时，从同次权威report.question/paper_ids及摘要status读取，不用UI示例填补。公开DTO附相对来源与SHA256，见 [来源清单](../acceptance/stage_g4_1/replay_manifest.json)。不接受用户filesystem path。

| 案例 | 展示事件 | 已审核Claim | 缺失单元 | 保留事实 |
| --- | --- | --- | --- | --- |
| G2 | 29 | 5 | 7 | Flamingo Worker协议耗尽，部分报告，比较0。 |
| G2.1 | 33 | 4 | 10 | Worker协议合法，Reviewer跳过10个不足，比较0。 |

事件数是原记录推导的展示事件数，不是原实验精确实时事件数。缺原逐阶段时间，timestamp=null；引用校验来自validation_status，合法空Claim通过结构门槛不代表存在已审核事实。

历史模型次数31/19、工具9/8、耗时约42.0/52.7秒均标历史。本轮回放Provider、检索、Embedding为0。模型Reviewer passed不是人工审核；14合法终态含10不足，不能称14个正确回答。

没有原字段显示“无记录”，不生成动作、正文或时间。必需文件缺失/非法返回replay_unavailable，未知case_id为404，损坏引用显示report_invalid。加载终态与原研究节点终态分开。

界面、证据、刷新游标、注入与无付费副作用验证见 [浏览器记录](../acceptance/stage_g4_1/browser_check.json)，属于真实历史材料的UI验证，不是新研究或质量评测。
