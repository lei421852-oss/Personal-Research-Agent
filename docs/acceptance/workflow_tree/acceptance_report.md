# 动态协作树展示验收

日期：2026-10-11。修改前本地 HEAD：`9a0c1e7`。

## 完成内容

仅修改现有工作台前端及浏览器检查、使用文档；Agent Policy、Controller、Worker、Reviewer、Recovery、Synthesis、Retrieval、API 契约均未修改。

- 无协作调用：正常线性展示主 Agent 及实际工具记录。
- 实际研究工具调用：自动切换树状图，以主 Agent → 深入研究工具 → 规划 / 论文分工 / 公共审核与恢复 / 综合角色组织记录。
- 按论文展示 Worker、引用验证与 Reviewer，节点只随事件出现；模型返回多个动作、两篇论文检索或问题包含比较字样，都不自动触发协作树。
- 历史默认研究入口没有主 Agent / 研究工具节点时，仅展示已保存的研究角色，不补画主 Agent。
- 没有论文或需求身份的审核保留公共分支；跳过 Recovery 或 `record_available=false` 的占位记录不绘制为已执行节点。
- 状态、原事件来源、时间与点击详情沿用现有记录。分支连线表达职责归属，不声称全部并行或推断缺失时间。
- 窄屏折为带缩进的单列职责树，保留各论文分支。

## 验证

| 项目 | 结果 |
|---|---|
| 全量 pytest | 1265 passed，0 failed，47.99 秒；1 项既有 Starlette/httpx 弃用警告 |
| Ruff | PASS |
| Python 格式检查 | 266 files already formatted |
| 冻结完整性 | 292 个冻结文件、47 个受保护产物一致 |
| 真实浏览器 | PASS，0 页面错误 |
| 付费模型调用 / Embedding / 重建索引 | 全部 0 |

浏览器检查覆盖：直接回答与领域工具保持线性；研究调用触发树状；首个 Worker 事件仅出现一个论文分支；完整协作中两个论文分支准确归属；同一节点不重复；点击 Worker 详情；中英切换；刷新恢复；390px 窄屏；G2/G2.1 真实历史只读回放。

模拟协作验证使用 Scripted 模型与现有领域链，截图仅用于界面展示，不作为真实模型研究质量评测。历史回放读取已有真实执行记录，不启动新的真实研究。结果详见 [browser_check.json](browser_check.json)、[test_results.txt](test_results.txt)、[lint_results.txt](lint_results.txt)、[format_results.txt](format_results.txt)、[integrity_results.json](integrity_results.json)。

## 使用

本次只更新静态前端，正在运行的工作台可通过浏览器强制刷新加载新界面，无需为了此布局重启后端。可点击顶部 G2/G2.1 历史回放立即查看真实研究记录的协作树，不产生模型费用。

之前 BLIP-2 / Flamingo 示例的实际运行是两个 `search_text` 后 `finish`，未调用研究链，因此该任务显示线性是预期行为。图形变化不改变模型自主路由，也不能把单 Agent 任务包装为多 Agent 协作。

保留原有未跟踪诊断文件；仅本地 Git 管理，不执行远程操作。
