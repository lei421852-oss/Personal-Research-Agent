# Stage 5.1.5：历史任务所有权审计

结论：目标任务 **UNKNOWN**。只读事务对 `personal_agent_ui_dev` 先执行 `SET TRANSACTION READ ONLY`，设置 3 秒 statement_timeout 并检查 Schema：`research_runs` 有 `owner_instance_id`，未发现 worker_id、lease_expires_at 或 heartbeat_at 列；项目在工作台启动时生成实例 UUID，Manager 在进程内调度 asyncio Task，未找到可用的数据库持久心跳或租约证明。

目标 `49c8d48d69ff415e880e7f8fc0554124` 仍为 `running`、state_version 1，owner_instance_id `8a9d561c4c034a7390f2d6f39e958395`，created/updated 时间均为 2026-10-30 03:15:25 UTC。当前关联事件和报告均为 0。历史执行记录表明当时创建返回 202、首次状态读取 503 / `run_persistence_incomplete`；这证明保存与后续读取异常，不证明当前 Worker 存活或死亡，也不解释首次持久化失败的根因。

观察到的两个 idle 数据库连接按客户端端口映射到临时只读工作台 PID 351364，而非目标任务所有者的证据；另有 PID 361139 的归属未知。没有可靠手段将历史 owner UUID 与现有进程一一绑定，也不能排除未见实例。任务不是已证实 ACTIVE，也不满足可靠失效证明；保持 UNKNOWN，不能自动修改、重试或接管。

若未来要维护：先暂停新任务入口，核实所有实例、相关在途调用和未知 PID；读取 run_id/status/state_version 及关联数据并记录备份；就具体操作及条件更新另行取得明确授权。失败或比较条件不匹配时中止并记录，不批量处理。当前不执行任何写入、删除、状态修复、终止或重启。
