# Stage 5.1.4：工作台诊断

核查时 PID 351364 以 `uvicorn stage513_workbench_readonly:app --host 127.0.0.1 --port 8000` 运行，cwd 为项目目录，端口仅监听回环地址，首页 HTTP 200。该进程是上阶段经授权建立的 `/tmp/stage513_workbench_readonly.py` 临时包装，连接真实开发库；HTTP 只允许 GET/HEAD，POST 拒绝，不等同正常项目启动入口或数据库级只读账号。未确认与历史任务存在 Worker 关联，不能据此证明没有其他实例。

服务启动于本阶段修复之前；未终止、重启或热加载它，因此它不能证明本阶段代码在运行中生效。`pg_stat_activity` 两个 idle 连接归属未能可靠归因，未观察到除审计自身外的活动事务；应用日志显示既有 GET 成功。旧 running 任务所有权未知，正常工作台恢复与新研究能力仍阻塞。无数据库业务写入或模型调用。
