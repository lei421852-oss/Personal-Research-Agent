# Stage 5.1.5：工作台诊断

现有进程 PID 351364 是 `/tmp/stage513_workbench_readonly.py` 包装的现有 FastAPI 工作台，监听 127.0.0.1:8000；从 `.env.ui-development` 读取配置并连接已核对的 `personal_agent_ui_dev`。HTTP 中间件只允许 GET/HEAD，其他方法返回 403。它是临时展示入口，**不是数据库账号权限意义上的只读事务保障**，也不是正常可提交研究的工作台；没有证据表明该进程持有旧任务。已观察其两个闲置 PostgreSQL 连接。

首页 200，论文目录与记忆 API 200，历史报告 API 200；`scripts.launch --check` 显示 UI、回放和本地语料索引就绪，Provider 为配置检查，未发送请求。不能把该健康预检解释为任务所有权、真实 Provider 或可安全执行研究已通过。

本阶段未启动/终止/重启任何服务，未修改配置；工作台进程在本阶段前启动，不能视为重启后加载最终提交的验证。UNKNOWN 任务及另一个未识别项目 Python 进程仍待核验，正常工作台恢复目前 BLOCKED。
