# Stage 5.1.5：运行实例清单

核查日期 2026-10-31，基线 `e9ebe11250c5f2b92cbf9a99c0e6710c5e0bb4ab`，分支 `feature/agent-memory`。本清单反映核查时 WSL 及可见 Windows 本机范围，不证明其他主机或不可见实例不存在。

| 对象 | 状态及证据 | 任务关联 |
| --- | --- | --- |
| WSL PID 351364 | 2026-10-31 20:25:08 启动，`python -B -m uvicorn stage513_workbench_readonly:app`，项目工作目录，监听 127.0.0.1:8000 | 临时只读展示；未发现其子进程，内存中的异步任务不可由进程清单完整枚举 |
| WSL PID 361139 | 2026-10-31 20:41:59 启动，`python -B -`，项目工作目录，无可见子进程、处于 epoll 等待；未观察到其 PostgreSQL TCP 连接 | 标准输入脚本的用途与任务归属无法可靠确认，不终止、不推断为 Worker |
| PostgreSQL | Windows PostgreSQL 18；开发库 172.24.0.1:5432，库与账号 `personal_agent_ui_dev` | 数据库服务，不等于研究 Worker |
| Docker | 核查时只见若干与其他项目有关的已退出容器，未见运行中的本项目研究/数据库容器 | 不停止、删除或重启任何容器 |

数据库 idle backend 5344/37152 的客户端端口分别为 53600/59194；WSL `ss -tnp` 同时显示这两个 TCP 连接属于 PID 351364，因此可将它们映射到只读工作台连接池，而不能据此推断其正在处理旧任务。审计查询自身使用独立连接。Windows CIM 全进程查询因访问拒绝未能完成，后以 `Get-Process` 确认 PostgreSQL 与 pgAdmin 相关进程；无法宣称全主机/其他机器完整实例清单。
