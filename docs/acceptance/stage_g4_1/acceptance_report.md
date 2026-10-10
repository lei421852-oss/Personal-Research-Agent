# Stage G4.1：多智能体工作台验收

**日期：2026-10-10。结论：PASS（工程集成、实际启动与真实历史回放）。**

本轮新增独立 Research API、进程内运行管理器、原生 Web 工作台和统一启动入口。复用原研究链，不修改冻结研究语义、原 API/UI、Corpus、Embedding 算法、检索排名或评测标签；未发起新的真实 Provider 研究。

## 1. 交付与复用

| 交付 | 位置与说明 |
| --- | --- |
| 多论文 API | [research_runs.py](../../../app/api/research_runs.py)：论文、健康、创建、状态、事件、报告、取消及历史案例。 |
| 任务管理 | [run_manager.py](../../../app/research/run_manager.py)：后台任务、运行1/排队2、重复拦截、取消收尾、历史20条/1小时。 |
| 事件与展示 | [run_events.py](../../../app/research/run_events.py)、[run_presenter.py](../../../app/research/run_presenter.py)：公开DTO、安全投影、记录派生状态、真实证据快照。 |
| 真实运行薄适配 | [workbench_runtime.py](../../../app/research/workbench_runtime.py)、[只读预检](../../../app/research/workbench_dependencies.py)：真实Planner及run_protocol_case，原默认Worker/协议适配，整体物理预算。 |
| 历史回放 | [run_replay.py](../../../app/research/run_replay.py)：固定六个源文件、来源指纹、无模型或检索。 |
| 页面 | [workbench.py](../../../app/workbench.py)、[index.html](../../../app/workbench_web/index.html)及CSS/JS；不引入框架或构建系统。 |
| 统一启动 | [launch.py](../../../scripts/launch.py)：本机服务和只读预检，不自动付费或下载模型。 |
| 使用与设计 | [快速开始](../../guides/ui-quickstart.md)、[架构与事件](../../architecture/workbench.md)、[回放来源](../../demos/workbench-replay.md)。 |

实际执行仍由原ResearchPlanner、PaperResearchWorker、DeterministicReviewValidator、EvidenceReviewer、RecoveryOrchestrator、SynthesisRuntime负责。没有复制研究循环或启用Coverage Worker。

## 2. UI与真实执行的对应

左栏显示当前真实Prepared论文5篇/46条文本/5条图像，并可选择2–3篇提交；25篇Paper Catalog未被伪装为正文语料。示例不自动执行，运行时阻止重复提交。

中栏显示Planner、并列Worker、引用验证/Reviewer、可选Recovery和Synthesis。live仅取真实生命周期/模型/检索回调；derived从返回结果推导，缺时间保留null；replay只读历史并明确标记。Worker实时身份来自程序绑定的Schema或真实检索scope，不通过关键词选择论文。

Validator没有独立生命周期，卡片只是实际validation_status汇总，不造起止时间。合法证据不足的not_reviewable显示skipped，审核Provider故障仍failed。Recovery显示实际资格与尝试，初始历史与最终合并记录分开。

右栏可检查Worker需求、动作、参数校验错误、协议失败、统计、证据页码和最终报告。原ResearchReport权威正文不被改写；未审核草稿不拼为事实。Evidence标report_snapshot及当前Corpus核对状态。模型passed与独立人审分开。

## 3. 本轮实际验证

以下均有本轮日志，见 [engineering_checks.json](engineering_checks.json)、[测试](test_results.txt)、[静态检查](lint_results.txt)。

| 检查 | 结果 |
| --- | --- |
| 完整pytest | **1,155 passed，0 failed，24.53秒**；原1,071项不变，本轮新增84项。 |
| Ruff | check app tests scripts eval：退出码0。 |
| Formatter | 249 files already formatted：退出码0。 |
| 冻结CLI | Stage E 292文件及原保护产物47项通过，退出码0。 |
| 启动预检 | python -m scripts.launch --check：UI/replay/real准备条件均通过，没有付费探测。 |
| 实际服务启动 | python -m scripts.launch --port临时端口：首页/静态/健康/论文/回放正常，SIGINT退出码0，原9个索引物理文件哈希不变。 |
| 真实历史浏览器检查 | 8项行为验证PASS，无浏览器异常；刷新不重复POST/事件，引用页码正确，HTML不执行，390px布局无溢出。 |

新增确定性测试覆盖请求验证、合法创建、重复/并发/队列、事件游标、真实取消与在途drain、预算与重试、超时部分快照、来源/证据一致性、缺Provider/字段/文件、递归脱敏和无启动副作用。Fake全领域链用于工程验证，不作为真实研究质量数据。

浏览器使用实际历史文件和明确禁用真实执行的验证入口：G2为29展示事件/5已审核/7缺失；G2.1为33事件/4已审核/10缺失，Reviewer通过4/跳过10。见 [browser_check.json](browser_check.json) 与 [launch_verification.json](launch_verification.json)。所有本轮真实Provider、检索、Embedding调用为0；历史31/19等次数不计为本轮成本。

截图：[G2协议失败详情](screenshots/g2-failure-details.png)、[G2.1原始证据](screenshots/g2-1-evidence.png)。截图字体只在临时验证环境引用，不提交字体或浏览器缓存。

pytest有一条既有Starlette/httpx弃用警告，没有失败；本阶段不改依赖。Ruff沿用已有0.16.5，Python3.12.3，未引入新Linter。

## 4. 文件保护与本地Git

开始于docs/stage-g3-technical-report，HEAD为58aa41581e136e0072eaad55dfec02dd3c05d2e6；原已跟踪工作区干净，13个未跟踪诊断文件保留。建立feature/stage-g4-1-agent-workbench。见 [baseline.json](baseline.json)。

| 保护组 | 数量 | 不一致 |
| --- | --- | --- |
| 原全部已跟踪文件 | 543 | 0 |
| 原未跟踪诊断文件 | 13 | 0 |
| 历史忽略目录内产物 | 40 | 0 |
| Stage E冻结清单 | 292 | 0 |

清单有重叠，不合计为独立文件总数。Stage E原指纹根44c7afd36c793eff86faed077b7c917a5c6a200e3d652bfbf5b19e83cff0362e不变。F2/F3.1-E/G2/G2.1/G3原产物及标签均在保护范围，见 [integrity_checks.json](integrity_checks.json)。

本地提交按研究管理/API、UI、启动/回放说明、测试/文档/验收整理；最终SHA记录在交付清单与Git历史，避免验收文档自引用未来commit。未覆盖或静默提交原诊断；未执行远程push/pull，未创建版本Tag。

## 5. 已知限制与停止边界

- 未执行新的付费真实研究；real_research_ready只是只读配置/缓存/索引条件，不证明Provider连通或新环境模型实际加载。
- 进程内状态，重启丢失活跃任务，不多进程共享，不适合作为生产队列；终态1小时/20条且可提前淘汰。
- 首次真实运行离线加载已有模型；取消/超时等待在途线程结束，420秒不是强杀上限。
- 细粒度阶段完成多数由结果推导；未记录的时间/字段显示无记录，不虚构全生命周期。
- 真实研究质量限制仍在：G2.1历史比较0、10个不足、无独立人审；界面不使这些结论变好。
- 新界面与scripts按源码运行；原冻结wheel打包范围不作发布保证，留给后续明确阶段。

G4.1达到本阶段工程与功能验收边界，无未解决阻塞项。完成后停止，不自动进入G4.2。
