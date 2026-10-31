# Stage 5.1.5：最终验收

**结论：BLOCKED；仅可继续只读展示，不进入 Stage 5.2。** 核心原因是旧 running 任务所有权仍为 UNKNOWN，同时一个项目目录下的 Python 进程用途未确认。没有自动接管、重试、修改任务或启动真实研究。

- WP-A：部分完成。只读工作台进程及其两个闲置 PostgreSQL 连接已识别；PID 361139 的用途不明，Windows CIM 全量查询受访问限制，其他实例不能排除。
- WP-B：BLOCKED。目标任务仍 running/version 1、事件和报告各 0；没有可持久验证的心跳/租约，无法证明持有者或确认失效。
- WP-C：CONDITIONAL。临时 HTTP 只读展示的首页、论文目录、真实记忆及指定历史报告可用；正常可写工作台未恢复，独立论文详情与完整服务端历史列表是当前功能缺口。
- WP-D：只读复核完成。保留上阶段有界故障验证及未验证时序；本轮没有重复数据库故障注入或新增持久化能力。
- Git 起始 `e9ebe11250c5f2b92cbf9a99c0e6710c5e0bb4ab`；未修改产品代码。按既有 manifest 逐项核对原 13 个未跟踪文件及 398 个历史产物 SHA256，均 0 差异。
- 本轮 `.venv/bin/ruff check app tests migrations`、`.venv/bin/ruff format --check app tests migrations`、`git diff --check` 均退出 0；300 个文件格式检查通过。按计划没有重复运行 980 项测试；980 passed 为历史 Stage 5.1.4 结果，非本轮结果。
- 开发库仅执行指定目标、只读事务和 statement_timeout 的查询；未执行 DDL/DML/迁移、未调用 Provider/arXiv、未启动研究任务。没有终止或重启现有实例。

后续先由维护者核对未知进程、所有可能实例及旧任务所有权；如需修改状态或重启，须提出具体操作并取得单独授权。真实模型仍需明确模型、预算、发送范围及新授权。本阶段不执行远程 Git 操作。
