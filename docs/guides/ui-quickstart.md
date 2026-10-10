# 多智能体研究工作台：安装、启动与使用

本指南对应 Stage G4.1。新界面复用 G2.1 原默认 Worker、协议适配、Reviewer、Recovery 和 Synthesis，不启用 F3.1 Coverage Worker。旧 `app.main` 单问题应用保持不变。

## 1. 一条命令启动

在仓库根目录、已激活的现有虚拟环境中运行：

```bash
python -m scripts.launch
```

打开 **http://127.0.0.1:8000/**。启动先只读检查环境、静态资源、Prepared Corpus、已有索引、本地模型缓存和 Provider 配置，分别输出 UI、历史回放、真实研究的准备状态。不会自动提交研究、发送付费请求、加载或下载模型、重建索引。

只检查、不启动服务：

```bash
python -m scripts.launch --check
```

端口被占用时：

```bash
python -m scripts.launch --port 8001
```

用 Ctrl+C 停止。运行中的研究先取消，再等待在途调用收尾；Python 线程不能被协程强杀，退出可能需要等待当前 SDK 请求超时。第一版只允许本机地址、单进程运行。

## 2. 新环境安装

Python ≥3.11。已有可用环境无需重装。新环境按现有依赖组安装：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,retrieval,vision,llm,data,api]'
```

按源码 checkout 运行；`scripts` 和新工作台静态目录不保证完整包含在原 V0.1 wheel 中。本阶段未修改被冻结的打包配置。

真实运行前需要现有固定 PDF/人工 prepared 数据、研究索引和 Qwen 本地缓存。已有项目直接复用；新机器按 [开发指南](development.md) 准备。启动预检不会执行写索引或准备语料命令。模型缓存、索引和凭据不要提交 Git。

## 3. Provider 配置

工作台读取根目录 `.env` 与进程环境，进程环境优先。使用已有字段：

```text
LLM_API_KEY
LLM_MODEL
LLM_BASE_URL
VLM_API_KEY
VLM_MODEL
VLM_BASE_URL
```

以 [.env.example](../../.env.example) 为模板，已有 `.env` 时保留。界面不允许修改地址、文件路径、Key 或系统命令；固定研究配置来自 [cross_paper_eval.json](../../configs/cross_paper_eval.json)。

**无 Key 仍可启动 UI、查看历史回放，真实提交按钮禁用。** 就绪只证明配置、缓存文件、Corpus 和索引绑定满足只读条件，不等于 Provider 已连通或模型已成功加载。首次真实提交才离线加载现有模型和装配客户端。

## 4. 页面使用

左栏填写问题，选择 2–3 篇论文。列表来自实际 Prepared Corpus：CLIP、BLIP、Flamingo、BLIP-2、LLaVA，共 46 条文本证据、5 条图像证据；未将 25 篇 Paper Catalog 全部当成可研究正文。示例问题只预填，不自动提交。

点击“开始真实研究”会启动真实 Planner → 原 Worker → 引用校验/Reviewer → 条件 Recovery → Synthesis。真实模型请求可能产生费用，模型审核通过不等于人工事实核验。执行期间锁定提交；服务器也拒绝相同问题/论文的活跃重复任务。

中栏展示真实数据驱动的节点。Worker 按论文并列；引用验证和 Reviewer 卡片汇总逐需求记录，点击可展开；Recovery 是条件分支。图由服务事件更新，不按前端定时器假定研究进度。

右栏查看需求、工具动作、错误代码、统计、实际报告、引用 PDF 页码和证据正文。证据保留 `report_snapshot`；`corpus_check=not_checked` 表示展示层未再次将历史快照与当前 Corpus 比较，不能读成已完成人工核验。

每秒轮询任务与事件游标；刷新恢复同一 `run_id`，不重新提交。取消期间显示“等待执行收尾”，实际收尾后才进入 cancelled。失败保留事件、部分结果和真正生成的报告。

## 5. 不花费 API 的历史演示

在“历史真实案例”选择并点击“查看历史回放”：

| 案例 | 原始结果 |
| --- | --- |
| G2 | 29 条派生展示事件；Flamingo Worker 协议失败；5 个已审核论断、7 个缺失单元。 |
| G2.1 | 33 条派生展示事件；Worker 协议合法；4 个已审核论断、10 个缺失单元；Reviewer 通过 4、跳过 10。 |

回放不调用 Provider、不检索、不重写原报告。页面明确标“历史回放”，次数和耗时均为原实验数据。回放 run.status=completed 仅表示文件已加载；历史失败、不足和比较数 0 均保留。这不是新的成功研究实验。

来源及缺失策略见 [回放来源](../demos/workbench-replay.md)，事件边界见 [工作台架构](../architecture/workbench.md)。

## 6. 预算和存储边界

同时运行 1 个真实任务、排队最多 2 个。总体模型物理请求最多 200，API 可降低到 1–200，包含 Planner、各 Agent、VLM 及实际依赖重试。原 Worker 局部预算继续生效，总体研究工具预算 64。420 秒等待超时触发取消和 drain，不是强杀线程的精确截止时间。

进程内最多保留 20 个任务，终态 1 小时，空间不足可提前淘汰最旧终态；活跃任务不会被回放覆盖。每任务最多 1,000 条事件，截断有标记。重启丢失当前任务，不支持多进程共享或生产分布式队列。已有 G2/G2.1 回放文件独立保留。

## 7. 检查与常见问题

```bash
python -m pytest -q
ruff check app tests scripts eval
ruff format --check app tests scripts eval
python -m scripts.verify_worker_coverage_baseline
```

沿用已有 Ruff。浏览器检查使用项目已有的可选 Playwright 工具链：

```bash
python -m scripts.check_workbench_ui --output-dir artifacts/workbench-ui-check
```

该检查显式禁用真实研究，读取历史材料。需要本机 Playwright Chromium；`--font-file` 只是截图选配已有字体，正常界面不依赖 Windows 字体路径。

| 问题 | 排查 |
| --- | --- |
| 命令/依赖不可用 | 确认在源码根目录并激活虚拟环境，检查现有依赖组。 |
| 真实提交禁用 | 刷新就绪状态；检查 Corpus、索引、缓存和六项 Provider 配置，不打印 Key。 |
| 索引绑定不一致 | 核对 collection、Corpus snapshot、模型配置；启动器不会静默新建索引。 |
| 首次真实任务较慢 | 本地模型加载、Provider 等待、重试和 drain 会消耗时间。 |
| 重复 409 / 排队满 429 | 查看已有任务，等待终态，不用连续点击增加调用。 |
| 取消后仍收尾 | 已停止新调用，在途线程真实结束后才能释放资源。 |
| 刷新找不到任务 | 可能重启、到期或超过保留上限；重新加载历史案例。 |
| 报告不足/跳过/降级 | 保留实际研究边界，不通过 UI 补写技术事实。 |

本阶段只验证工程集成和真实历史回放，未启动新的真实 Provider 研究。见 [G4.1 验收](../acceptance/stage_g4_1/acceptance_report.md)。
