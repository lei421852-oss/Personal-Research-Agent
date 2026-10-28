# P1：生产工具接入适配层验收报告

日期：2026-10-28（Asia/Shanghai）

## 1. 验收结论与范围

**P1 PASS：本地适配层工程验收通过。真实生产工具服务尚未指定、未连接、未部署，因此本结论不代表生产服务已验收。**

本轮完成B2正式决策登记、工具架构审计与ADR、最小外部工具适配层、原ReAct接入、78项新增确定性测试、完整离线回归、本地7场景演示及文档。未启动P2。

实际操作边界：真实arXiv请求0次、付费模型调用0次、数据库连接/迁移/DDL/DML 0次、远程Git操作0次。允许的本地stdio子进程与原有回环HTTP故障测试不属于真实外部服务调用。

## 2. 基线与B2正式登记

| 项目 | 记录 |
|---|---|
| 实施前分支 | feature/h2-2-research-persistence |
| 实施前HEAD | b76306bec67c8b975d8322a2c2ace090f98af831 |
| 已跟踪/暂存差异 | 实施前均为空 |
| 原有未跟踪文件 | 13个，逐个路径与SHA256保留 |
| P1分支 | feature/production-tool-integration |
| B2登记/计划提交 | 38d723a62b299d7ae5c245a5a3043c4b5c02be35 |
| 实现与测试提交 | a1c777d4e294a2a9089cb712b95d162b7cf95d07 |

正式用户决定按原B2工程范围PASS。已更新原acceptance.json已有的conclusion、real_tool_acceptance、blockers三个字段，未增加其顶层Schema字段。原JSON更新前完整字节备份于：

`artifacts/p1-external-tools-2026-10-28/b2-acceptance-before-decision.json`

正式决策、更新前后哈希、历史阻塞描述和B2-R2报告摘要登记在：

`docs/acceptance/b2-final-decision.json`

原B2及10月26日B2-R1超时观察、失败JUnit、报告均保留。原JSON中的历史测试结果与调用计数保持原值，其日期是原执行日期；本次决定日期在独立决策文件中记录，不能把历史失败改称通过。B2-R2审查报告SHA256：

`e41a4e322c41987256dcc71a2b7dfc71b7e0b8df6e7c79c8058d3c623eacd3ea`

本次PASS不意味着历史网络根因已经定位，也不意味着长期可用性得到证明。

## 3. 实际修改文件与架构

只修改了一个原有生产文件：`app/research/react_runtime.py`，增量加入可选来源、注册与finally关闭钩子。原Registry、Executor、BoundedToolAgent、主策略、Planner/Worker/Reviewer/Recovery/Synthesis、arXiv实现、数据库、UI、依赖锁和迁移均未修改。

| 文件 | 职责 |
|---|---|
| app/tools/integrations/models.py | 显式配置、白名单、权限及发现定义 |
| app/tools/integrations/schema_mapper.py | 严格JSON Schema子集到Pydantic模型 |
| app/tools/integrations/error_mapper.py | 映射至原Observation和issue |
| app/tools/integrations/transport.py | 有界stdio传输、deadline、取消、进程监督 |
| app/tools/integrations/adapter.py | 能力发现、原子注册预检、Tool包装与会话 |
| app/tools/integrations/__init__.py | 无连接副作用的包入口 |
| app/research/react_runtime.py | 可选来源进入原Registry/预算与原取消路径 |
| scripts/smoke_external_tools.py | 7场景纯本地演示与可配置输出目录 |
| tests/unit/tools/integrations/ | Schema/权限/契约/输出/取消清理测试及本地服务 |
| tests/test_external_tool_integrations.py | 原ReAct反馈、按需多Agent、预算与取消接入测试 |
| docs/architecture/production-tool-integration.md | 协议、配置、架构、限制与复现命令 |
| docs/acceptance/p1/audit-and-plan.md | 实施前审计、ADR和测试矩阵 |
| docs/acceptance/b2-final-decision.json | 用户正式B2决定 |
| docs/acceptance/p1/acceptance.json、acceptance_report.md | 本阶段正式验收记录 |

完整实现/测试文件SHA256见验收JSON；最终文档提交ID见交付证据索引或Git对本目录的日志。记录不把自身提交SHA写回自身内容，避免自引用。

~~~mermaid
flowchart TD
    Q["自然语言问题"] --> A["原主Agent自主动作"]
    A --> R["原Registry / 严格Schema"]
    R --> E["原Executor + GuardedTool共享预算"]
    E --> P["Adapter：启用/白名单/只读权限"]
    P --> C["stdio客户端：单请求子进程"]
    C --> S["本地受信任模拟服务"]
    S --> O["原Observation / Trace"]
    O --> A
    A --> M["可选原多Agent研究或finish"]
~~~

没有新建并行注册体系、全局状态或新的Agent决策入口。工具数量变为可选扩展，不改变是否启动多Agent的语义策略。

## 4. 协议、配置与依赖决定

选择**受限的项目内部JSON-over-stdio v1，不是MCP**。当前仓库无可复用MCP实现，也没有指定真实服务；因此不引入SDK或臆造远程接口。新增第三方依赖为0。

工具命名：`external__provider__tool`。采用双下划线命名空间，避免点号与现有OpenAI-compatible函数名约束冲突；已读取本地安装SDK的function_definition说明核对该限制。

工具定义支持发现、Schema读取、调用、分类错误、取消、超时和输出限制。首版仅支持管理员批准的本地只读来源，命令不来自模型或工具返回内容。默认外部来源为空，enabled=False；仅改环境开关不会创建服务，必须由受信任宿主显式注入来源。

可选环境读取：

~~~dotenv
AGENT_EXTERNAL_TOOLS_ENABLED=false
AGENT_EXTERNAL_TOOL_TIMEOUT_SECONDS=5
AGENT_EXTERNAL_TOOL_MAX_OUTPUT_BYTES=65536
~~~

配置直接使用现有独立环境字典模式，不写入.env或变更全局持久配置。默认超时5秒、最大输出65536字节；工具列表由管理员显式白名单控制。没有生产认证配置，因为服务尚未确定。

Schema支持严格object/string/integer/number/boolean/array、必填、可选省略、字符串enum及基本边界。object必须additionalProperties=false。未知特性拒绝，不静默放宽；深度最多4、节点128。未给出的可选参数不会转换成错误的显式null。

## 5. 安全、失败语义与生命周期

| 检查项 | 实现与实际验证 |
|---|---|
| 未授权/有副作用工具 | 不注册、不调用；服务自述不能授予权限 |
| 非法参数 | 原Executor严格校验；外部call_tool进程次数0 |
| 名称冲突 | 整批预检后注册，冲突不覆盖、不半注册 |
| 输出限制 | 最多读取上限+1字节即失败并回收；不先无界读取再截断 |
| 输入限制 | 编码前限制JSON深度/总量，编码时再次检查UTF-8字节数 |
| 总预算 | 锁、管道、传输、映射与结果规范化沿用同一截止时间 |
| 取消 | 使用原请求Event；在途子进程停止，原Runtime传播CancelledError |
| 重试 | 每操作最多一个子进程，automatic_retries=0 |
| 清理 | 正常/错误均关闭管道、selector并回收；不确认则cleanup_failed且阻断后续进程 |
| 敏感信息 | 父环境不继承Provider/数据库凭据，错误只给固定代码；输出沿用原脱敏 |
| 引用边界 | 返回只放external_result；伪Evidence ID不成为合法finish引用 |
| Agent路径 | 工具可用不等于强制调用；原多Agent选择和预算保持 |

公共envelope仍是success/empty/error，issue仍使用原枚举。精确类别保存在external_error_code；timeout/unavailable映射dependency_failure，取消/清理/协议错误保留具体代码，不伪装empty。

既有契约只允许dependency_failure的retryable=true；它不构成Adapter自动重试授权。首版所有操作均零自动重试。普通取消仍保持取消语义，不作为合理的证据不足替代。

Trace保留原tool_call_id、名称、参数、状态、耗时和证据数量。外部数据没有自动成为Evidence，因此result_count为0；执行元数据另外记录request_id、操作、次数、输出字节、PID、returncode、reaped与stop_reason。不记录隐藏思维链。

## 6. 本轮实际测试结果

| 检查 | 实际结果 | 耗时与退出码 |
|---|---|---|
| 新增适配层/ReAct定向测试 | 78 passed，0 skipped，0 failed | pytest 3.68秒；退出0 |
| 既有相关回归 | 207 passed，0 skipped，0 failed | pytest 7.02秒；退出0 |
| 完整离线回归 | 1701 passed，56 skipped，0 failed，1 warning | pytest 67.85秒；进程68.37秒；退出0 |
| Ruff check . | All checks passed | 退出0 |
| Ruff format --check app tests migrations | 269 files already formatted | 退出0 |
| 新脚本格式检查 | 1 file already formatted | 退出0 |
| git diff --check / staged diff --check | 无错误 | 退出0 |
| 演示CLI --help | 参数解析成功 | 退出0 |
| 本地stdio模拟演示 | 7/7场景符合预期 | 退出0 |

78和207是完整回归中的重叠子集，不与1701相加。新增测试相对当前基线为78项；完整通过数较此前1623增加至1701。

56项跳过全部为未经本轮授权的真实集成测试：arXiv 4项、PostgreSQL 52项，未计通过。逐文件数量：

- `tests.integration.test_admission_postgresql`：3。
- `tests.integration.test_arxiv_b2_live`：2。
- `tests.integration.test_arxiv_live`：2。
- `tests.integration.test_database_infrastructure_postgresql`：3。
- `tests.integration.test_end_to_end_postgresql`：9。
- `tests.integration.test_manager_persistence_postgresql`：2。
- `tests.integration.test_persistence_write_regressions_postgresql`：9。
- `tests.integration.test_persistent_reads_postgresql`：6。
- `tests.integration.test_repository_postgresql`：15。
- `tests.integration.test_writer_postgresql`：5。

唯一警告为已有Starlette/httpx弃用提示，不是本轮新增功能失败。未为消除警告改变依赖版本。

初轮新增测试72通过、2失败，均为新夹具：敏感字段名被既有脱敏隐藏、named论文研究输入问题缺少对应范围。修正夹具后通过，未降低旧断言。Ruff发现的本轮循环变量绑定和新测试导入问题已修正。完整回归后只补了一处新测试导入空行，并通过AST比较确认无语义变化，生产代码未再改变；随后最终静态检查通过。

实际命令（仓库根目录）：

~~~bash
.venv/bin/python -B artifacts/p1-external-tools-2026-10-28/run_offline_checks.py targeted
.venv/bin/python -B artifacts/p1-external-tools-2026-10-28/run_offline_checks.py related
.venv/bin/python -B artifacts/p1-external-tools-2026-10-28/run_offline_checks.py full
.venv/bin/ruff check .
.venv/bin/ruff format --check app tests migrations
.venv/bin/ruff format --check scripts/smoke_external_tools.py
git diff --check
.venv/bin/python -B -m scripts.smoke_external_tools --help
.venv/bin/python -B -m scripts.smoke_external_tools --output-dir artifacts/p1-external-tools-2026-10-28/smoke
~~~

受限测试入口在pytest导入前关闭真实授权、清除测试数据库目标并强制模型离线；父进程阻止非回环/数据库连接。三组记录的被阻止连接尝试均为0。子进程仅执行本地测试服务或原本回环故障测试，不执行真实官方接口。

复现无需真实Provider、数据库或额外工具SDK：

~~~bash
.venv/bin/python -m pytest -q tests/unit/tools/integrations tests/test_external_tool_integrations.py
.venv/bin/python -m scripts.smoke_external_tools --output-dir artifacts/my-p1-local-smoke
~~~

演示文件排他创建；重复运行用新的输出目录，不能覆盖已有证据。

### 6.1 七个本地场景

| 场景 | Observation | 原issue | 实际call_tool进程数 | 回收 |
|---|---|---|---|---|
| success | success | 无 | 1 | 全部已确认 |
| empty | empty | empty_result | 1 | 全部已确认 |
| invalid_input | error | invalid_arguments | 0 | 全部已确认 |
| unavailable | error / unavailable | dependency_failure | 1 | 全部已确认 |
| timeout | error / timeout | dependency_failure | 1 | 全部已确认 |
| cancelled | error / cancelled | internal_failure | 0 | 全部已确认 |
| output_limit | error / output_limit | internal_failure | 1 | 全部已确认 |

cancelled演示是调用前取消、因此0进程；新增测试另外覆盖在途取消、排队超时、close中断、输入管道不读、输出后不退出，以及完整Runtime取消传播。模拟“回收不确认”测试实际先回收测试进程，再模拟无法确认，验证fail-closed，不制造真实泄漏。

## 7. 文件保护与凭据检查

- 原803个已跟踪文件只有明确授权的react_runtime.py发生内容变化；没有移动、删除原模块。
- Stage E共292项。本轮292项均保持实施前内容；其中291项匹配最初冻结哈希，根README是此前已授权的既存文档例外，未被本轮改动。
- B2/B2-R1三处历史目录301项中，只有明确授权的acceptance.json状态字段更新；其余300项原始证据哈希一致。
- 原acceptance完整字节备份与原SHA256相同，新旧顶层字段集相同。
- 依赖、锁文件、Schema与Alembic历史没有变化。
- 原13个未跟踪文件SHA256全部一致，没有暂存、删除或覆盖。
- 16个实现/测试/演示文件的模式扫描只发现明确的测试假密钥；没有真实凭据。没有暂存.env、本机认证文件、缓存或原诊断材料。
- 未重新审计所有非B2的忽略目录；不把未实施的全量历史复核写成通过。已跟踪历史与明确保护文件有本轮基线比对。

原13个文件前后相同：

| 相对仓库路径 | 前后SHA256 |
|---|---|
| `docs/diagnostics/quality_failures_2026_10_09/artifact_manifest.json` | `ee233e77df173293c437e887f78c141c6d36dcf264d6b004c0223853fd60f223` |
| `docs/diagnostics/quality_failures_2026_10_09/findings.json` | `f5590827026f11b87fbbc0729f0bbc2e6e26b4c1399af337980cf8c638cc93de` |
| `docs/diagnostics/quality_failures_2026_10_09/report.md` | `9579aabe3451eae73aa949beee92133103dbc0168ede980a0f0070d4851a099f` |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-frozen-trainable-scope.md` | `abdec5284ce2de37d786cbd2bc66d8c49c23eb045fa1f9f64c8bdedb3b7c3f81` |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-language-model.md` | `51c5dada6a6cdded716bd1a0bf670dee83a33e8f64558b03dfb321b60385508c` |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-training-objective.md` | `b50a0e2ecc96c50e93b83e7d3bd0c2ccedb2aec1b48106539ff4bcee5bccbb29` |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-vision-encoder.md` | `e62e2404f29e15cf4233a319dd69952ab0cbd28b73371ad74182e4e9260f797c` |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-flamingo-vision-language-bridge.md` | `cb0b57c84f80526ea0d89a90e2213fd261649f5ea3a57414efc732323fa48476` |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-llava-frozen-trainable-scope.md` | `f1422c861901636998acd0eb45a379900e8fbe5f2bc8fb9a2db6e4290eaf5d09` |
| `docs/diagnostics/quality_failures_2026_10_09/rqv1-comparison-blip2-llava-language-model.md` | `1156f7e4efc7ec8731be1e539753717b659aae1b76ebf398d7338fa2a219b936` |
| `docs/diagnostics/quality_failures_2026_10_09/verification.json` | `f107553f46a803cd866a3d16a9892bdc5682bde958d2b4eecba98759a7f2fca3` |
| `scripts/export_quality_failure_traces.py` | `b59cbb413bad0cf93936c28fd0896f2ba952aa532f6b4d945d87804f2185c23c` |
| `tests/research_quality/test_failure_trace_export.py` | `9a84da8a40e69b3b834085fc4c2725de495dfb2893e04517d84c05cc169198bc` |

## 8. 证据路径与Git交付

正式源文件、测试和文档使用本地Git；未执行远程操作。运行日志与缓存遵循原artifacts忽略规则，不强行加入Git。

主要原始记录位于 `artifacts/p1-external-tools-2026-10-28/`：

- baseline.json：实施前803个已跟踪文件、13个未跟踪文件与历史证据哈希。
- targeted.xml、related.xml、full.xml及对应txt/execution.json：实际命令、退出码和结果。
- targeted-initial.xml/txt：初轮夹具失败记录，保留。
- release-static-checks.json及ruff-release/format-release/diff-release.txt：最终静态结果。
- smoke/observations.json：7个本地场景Observation、Trace与实际传输记录。
- integrity-final.json：保护核对。
- source-audit.json：提交范围与凭据模式扫描。
- implementation-commit.json：实现提交与明确文件清单。
- post-full-format-only.json：完整回归后的测试空行变更AST一致性。
- acceptance-evidence.json：结构化汇总；最终交付索引另记录文档提交ID。

tracked正式验收：`docs/acceptance/p1/acceptance.json`、本报告和架构说明。验收文档提交不包含原有13个未跟踪文件。交付完成后已跟踪工作区与暂存区应干净，仍保留原13个未跟踪文件。

## 9. 已知限制与未执行项

1. 真实生产服务未指定，未做生产连接、鉴权或部署验收。
2. 协议是受限私有stdio而非MCP；没有远程HTTP/SSE或通用插件发现。
3. 必须由受信任宿主显式装配来源；环境开关本身不会启动服务。
4. 仅只读白名单，不支持副作用工具、自动补偿或写调用重试。
5. Schema为严格子集，复杂定义会拒绝，不降级为宽松参数。
6. 子进程执行环境为WSL/Linux，不是OS安全沙箱；不保证宿主机冻结和恶意脱离进程组程序的强制隔离。
7. Scripted测试验证路径，不证明真实LLM选择最优，也不是新的研究质量Benchmark。
8. 外部数据尚不允许直接成为论文事实、Evidence或Citation；需现有证据链确认。
9. 没有新增记忆、Redis、数据库表、UI或研究策略，没有重跑真实论文研究。

没有未解决的本地工程验收阻塞项。上述限制是P1的明确边界，不伪装为真实服务已通过。

## 10. 最终验收清单与停止点

- [x] B2正式PASS登记，原失败证据与旧JSON完整备份保留。
- [x] 原架构审计、协议ADR和最小模块实现完成。
- [x] 本地服务通过原Registry/Executor进入ReAct反馈链。
- [x] 权限、严格参数、超时、取消、输出、协议和清理失败有确定性测试。
- [x] 既有相关回归及完整离线回归通过。
- [x] 依赖和冻结边界保护，未接入未指定生产服务。
- [x] 架构、配置、错误语义、命令和限制文档完成。
- [x] 本地提交明确，未执行push/pull。
- [x] 未启动P2或真实端到端研究。

后续真实接入仍需明确：服务来源与协议、认证注入方式、只读工具白名单、限额与数据边界。若改用MCP或有副作用工具，须单独Review，不能绕过本阶段边界。

**本阶段到此停止，等待独立Review。**
