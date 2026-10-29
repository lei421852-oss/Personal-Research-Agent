# H2.2：研究运行持久化使用与故障处理

本指南对应阶段2.5的实际代码。默认仍为内存模式；启用数据库且使用默认Manager装配时，Run、Event、Report会写入PostgreSQL，状态/事件/报告三个GET以已提交记录为依据。回放仍是独立文件来源，不写成真实研究。

## 安装与配置

基础依赖和数据库依赖沿用已有锁文件。在源码根目录的独立虚拟环境中安装：

```bash
python -m pip install --require-hashes -r requirements-runtime.lock -r requirements-persistence.lock -r requirements-h2-test.lock
python -m pip install --no-deps -e .
python -m pip check
```

迁移工具另见`requirements-migrations.txt`。当前尚无`requirements-migrations.lock`；安装该输入文件不等于完整的哈希锁定复现。此缺口保留为后续工作，不在本阶段调整依赖配置。完整研究回归仍依赖既有retrieval/vision/data资源，最小安装通过不能替代完整测试。

数据库设置只读取进程环境或显式注入值，不主动加载`.env`。`H2_DATABASE_ENABLED`默认false；启用必须安全注入`H2_DATABASE_URL`。URL必须使用postgresql+asyncpg，明确主机、库名、账号、密码，只接受ssl参数disable/require/verify-full，默认require。不要在源码、ini、命令历史、日志或报告中保存完整DSN。

| 运行方式 | 实际行为 |
|---|---|
| 默认禁用 | 沿用内存与回放；不导入SQLAlchemy/asyncpg/业务ORM |
| 数据库启用，默认装配 | create_app装配ResearchRepository、TerminalCoordinator和RunPersistence，创建及三个GET走数据库 |
| 数据库启用，注入manager_factory | 由调用方提供持久化栈，create_app不代替调用方装配 |

**当前没有另一个H2_PERSISTENCE_ENABLED开关。打开H2_DATABASE_ENABLED并使用默认装配，就会启用业务持久化，不再只是连接健康检查。**

应用账号与迁移账号分别配置。迁移唯一读取`H2_MIGRATION_DATABASE_URL`，不回退应用URL；应用启动不执行create_all、upgrade或stamp。已有启动入口仍是：

```bash
python -m scripts.launch
```

## 任务、事件与报告

- 数据库创建记录可靠提交前，不启动研究Task；同键首次创建只有获胜者有调度资格。
- `request_payload`保存完整已验证请求，包括原问题、请求paper_ids、language_mode和max_model_calls。活跃fingerprint不是永久幂等键，也不替代完整请求hash。
- 事件由Manager主事件循环分配序号，经过公开字段脱敏后进入现有有界串行Writer。event_id、source及发生时间保持真实；derived/replay缺少时间时不伪造为实时事件。
- 事件高水位仅表示连续已提交前缀。队列中待写数据不是已提交历史；GET不会以缓存掩盖数据库不可用。
- 报告revision在命令构建期预分配并冻结；重试不自动换号。读取复核沿用唯一report_content_hash口径，覆盖schema_version、snapshot_kind、public_payload、authority_payload、source_reference。
- 最终报告、指针、尾部事件高水位、状态、finished_at及终态token/hash按现有Repository事务提交。报告状态、答案状态与运行状态是不同词表，不能互相推导。
- 默认内存TTL只清缓存，不删除数据库历史；首版没有新增历史列表、搜索或UI。

## 论文范围的两种语义

| 字段 | 含义 |
|---|---|
| request_payload.paper_ids | 原始请求候选/目标范围，选择后也不覆盖 |
| effective_paper_ids | 已确认的实际研究范围；只有真实选择完成，或Planner已取得明确范围时才写入 |
| public_state.paper_selection | 已提交的最小选择标记：status=selected和selected_paper_ids；不是完整选择理由或模型统计 |

实际范围由PaperSelection完成事件或带明确paper_ids的Planner开始事件确认。主Agent可以启动多个研究子任务，持久化保留这些真实确认范围的有序并集；去除空白与重复ID，不自动补论文或改写ID。

选择确认前，effective_paper_ids仍为空且没有selected标记；任务在选择前失败不把输入范围伪装成选择结果。选择写入与事件走同一串行Writer，终态flush等待两者完成。已有CAS更新保留首次started_at，不因补充选择元数据而重置开始时间。

状态GET和同键重放中的paper_ids优先取数据库已提交effective_paper_ids；未确定时为兼容旧DTO退回数据库request_payload.paper_ids。**这时paper_ids表示请求范围，不能把非空输入误读为实际已选结果，应同时看paper_selection标记。** 不使用尚未提交的内存paper_ids冒充数据库事实。

重启后仍从相同列读取范围。research_question、query_language等未覆盖写路径的展示信息仍可能为null或空字典；answer_status、execution_mode、report_language_status依赖内存展示补充时也可能为null。报告GET是报告正文、引用与版本信息的权威来源，不能从状态展示字段的缺失推断报告已丢失。

## HTTP错误与重放

保持Idempotency-Key及Idempotency-Replay-Only契约；同键读取先于普通活跃指纹检查。只查重放不创建记录、不调度、不调用Runner。单实例内提交/注册窗口的admission逻辑不变。

| 场景 | HTTP / code | 下一步 |
|---|---|---|
| 新建且记录提交、Task安排成功 | 202 | 按原run_id轮询 |
| 同键同请求、本实例持有或可靠终态 | 200，Idempotency-Replayed=true | 不执行第二次研究 |
| 同键不同完整请求 | 409 idempotency_key_conflict | 不用修改后的请求复用原键 |
| 当前内存已知活跃重复 | 409 duplicate_run | 查原运行，不新建 |
| 重启后数据库明确给出活跃指纹唯一冲突 | 409 duplicate_run | 无第二条run、无额外Runner调用；不接管旧任务 |
| 非终态但本实例未持有 | 409 run_not_attached | 原run只读查询，必要时人工处理 |
| 只查重放未找到记录 | 404 submission_not_found | 不自动切回创建 |
| 数据库确实无该run | 404 run_not_found | 区别于存储故障 |
| 数据库不可用 | 503 storage_unavailable或storage_pool_timeout | 不冒充404或空结果 |
| 创建提交结果未知/未识别的创建异常 | 503 submission_outcome_unknown，retry_mode=reconcile_only | 同键只读核对，不自动重建或调度 |
| 调度窗口结果仍未知 | 503 task_scheduling_outcome_unknown，reconcile_only | 不由其他请求补建Task |
| 已确认调度失败并可靠失败收尾 | 500 task_scheduling_failed，沿用既有safe_retry语义 | 不在原请求内自动安排；新研究需显式新请求 |
| 已知写入/收尾/内容校验不完整 | 503 run_persistence_incomplete，reconcile_only | 不拿内存completed/报告冒充成功 |
| 尚无报告 | 409 report_not_available | 已有run与报告缺失分开表达 |
| 非法游标 | 422 invalid_cursor | 修正after |

仅Repository明确识别`ConflictError.code=duplicate_active_fingerprint`且约束名为`uq_research_runs_active_fingerprint`时，创建阶段才进入新增409分支。不能把所有IntegrityError、ConflictError或超时归为409；约束名无法识别时也不能误报CREATED。其余创建异常维持保守503/reconcile_only，不扩展提交阶段判定协议。

StorageError继续使用既有信封`status=unavailable,error={code,message,retryable}`；RunManagerError继续使用其既有信封，不在本轮强行统一。

## UNKNOWN、FOREIGN_TERMINAL与研究失败

`UNKNOWN`不等于`rolled_back`。已经发出COMMIT后无法确认结果的终态尝试，永远不能自动重新进入写路径，只能调用独立的只读reconcile_finalize。`finalize_run`与`reconcile_finalize`不是同一个操作，后者不写数据。

- CONFIRMED：当前token与内容hash对应的已提交事实得到核对。
- FOREIGN_TERMINAL：数据库有其他token的终态；保留数据库结果，不覆盖，也不能据此声称当前token提交成功。
- STILL_PENDING/UNKNOWN/UNAVAILABLE：不自动重新finalize、不换token重写，按已有latch和错误契约保留待核对状态。
- 终态写入只有已证明rolled_back的尝试，才可按既有规则复用同一不可变命令重试。

创建阶段提交结果未知同样维持503/submission_outcome_unknown/reconcile_only；本阶段不增加新的“已知未提交”判定协议。数据库中可能确实有queued记录，也可能没有，不因猜测主动补建。

`insufficient_evidence`是研究材料不足的业务结论；数据库故障、写入失败、执行中断不是证据不足。不得把它们写成已完成、无证据或可靠拒答。

## 遗留任务与人工维护出口

首版没有分布式锁、自动接管、自动重排、Checkpoint/Resume，也没有启动时自动清理。owner_instance_id只记录归属，不证明旧实例死亡。

提交已落库但结果未知时，queued/running记录可能长期占住活跃fingerprint。同指纹新请求此时返回已知duplicate_run，不代表旧研究正在本实例执行，也不意味着允许接替。不要通过删除记录、改幂等键或绕开索引自动重跑。

维护必须满足：

1. 停止新研究入口和自动创建重试，确认全部应用实例及在途Provider/工具调用、写事务已停止；仅凭端口无响应或本地无Task不足以判死。
2. 在已授权只读访问中列出run_id、owner、status、state_version、finalize_token/hash、报告revision、事件高水位及相关内容hash。
3. 维护者逐项确认目标与原因，并明确授权该次DML；保留操作前清单。不能用本指南或旧测试授权代替正式数据库写授权。
4. UNKNOWN对应的旧token不自动重写。若所有权与停止条件确认后需要结束遗留非终态记录，由维护者批准一次新的人工收尾操作，基于已提交状态构建新token、UTC结束时间及真实失败原因；不能声称旧token已成功。
5. 通过既有Repository终态契约，以预期status/version、报告revision和事件高水位作条件，核对影响范围。不得仅UPDATE status而绕过终态token/hash/finished_at一致性CHECK；不删事件/报告，不改原请求、幂等键或既有报告hash。
6. 读回并保存前后状态、版本、token、报告指针与hash、高水位、授权依据及结果。不满足预期则停止，不盲重试；确认单实例配置与存储就绪后再恢复入口。

本指南不提供自动执行的清理CLI、生产UPDATE或downgrade命令。终态FOREIGN结果应只读采用并记录来源，不能为释放名额而覆盖旧终态。

## 健康检查与已知边界

`GET /api/storage/health`仍然是`scope=connection_only`：禁用时不连接，启用时只执行SELECT 1。ready不表示业务迁移版本正确、所有写入已提交或报告存在。研究health与数据库health也不是同一个指标。

当前尚未实现命名的storage_schema_mismatch守卫、foreign_active_runs专门出口或完整public_state展示写入。本阶段不新增这些功能，迁移前应独立核对h2_0001；无自动接管/重跑的安全约束继续有效。迁移锁文件、TLS/证书、服务端崩溃和长期负载等未验证项不因阶段2.5通过而消失。

## 测试与安全目标

离线回归：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check app tests migrations
git diff --check
```

业务PostgreSQL测试只在维护者确认独立、可销毁目标后，安全注入：

- H2_TEST_PERSISTENCE_AUTHORIZED=yes
- H2_TEST_PERSISTENCE_DATABASE_URL
- H2_MIGRATION_DATABASE_URL（仅迁移进程需要，不回退应用URL）

这些用例不只有SELECT，还含迁移、DELETE清理、受控篡改和错误注入。**严禁指向agent_learning、生产库或需要保留的数据。** 应使用专用临时容器、回环绑定、独立数据目录和明确清理范围；运行器先验证目标所有权和隔离，再执行既有Alembic迁移。

授权目标准备完成后可沿用既有入口：

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m pytest -q tests/integration/test_persistence_write_regressions_postgresql.py
.venv/bin/python -m pytest -q tests/integration/test_admission_postgresql.py
.venv/bin/python -m pytest -q tests/integration/test_persistent_reads_postgresql.py
.venv/bin/python -m pytest -q tests/integration/test_end_to_end_postgresql.py
```

不使用付费Provider；Fake Runner用于研究流程输入，数据库连接、事务、约束和读写是真实PostgreSQL。授权门控未开启时跳过真库测试，不计为通过。默认禁用模式、原研究链与冻结/历史材料仍需完整回归。
