# P1 架构审计与实施决策

日期：2026-10-28。基线：b76306bec67c8b975d8322a2c2ace090f98af831。

## 审计结论

- ToolRegistry.register/get/schemas 支持动态注册、重复名称拒绝；无需修改。
- ToolExecutor.execute 同步校验Pydantic模型、执行、结果校验、脱敏和Trace；不提供通用异步调用或全局超时。
- BoundedToolAgent 与 MAIN_POLICY 决定动作，已有模型/工具预算、单动作纠正和工具反馈；保持原样。
- ReActWorkbenchRuntime通过_offload和AsyncDecisionBridge将同步循环桥接到异步模型；GuardedTool保留统一工具预算。新增工具应在生成guarded注册表前注册。
- arXiv的子进程监督仅适用于arXiv；不改、不另叠重试。外部stdio调用在自身传输边界监督阻塞进程，沿用该请求取消信号。
- 持久化仍由RunManager/Repository/Writer负责，适配器不触碰数据库。现有sanitize_public与Executor负责公开信息脱敏。
- 仓库没有MCP实现/配置；MCP SDK未安装。jsonschema虽为传递依赖，但未在项目直接声明，不依赖它。
- 真实arXiv与PostgreSQL测试有独立授权门控；本轮全部关闭。普通回归包括仅回环的arXiv故障测试，需与外部真实调用区分。
- Stage E冻结292项中仅根README已有经授权文档例外；Registry、Executor、主循环与pyproject均冻结，禁止改动。react_runtime不在该冻结集合。
- 原有13个未跟踪文件哈希已保存于artifacts/p1-external-tools-2026-10-28/baseline.json；不得清理或暂存。

## ADR-P1-001：先用受限本地JSON/stdio，不宣称MCP

当前没有指定真实工具服务，也没有可复用协议实现。本阶段选择版本化的单请求/单响应JSON over stdio：list_tools和call_tool。它是项目内部协议，不是MCP。仅本地受信任管理员提供命令，模型/服务返回内容不能改命令或权限；默认禁用。

不新增依赖：仅Python标准库和已声明的Pydantic。后续明确服务来源后可另审MCP客户端；本阶段不模拟兼容标准、不接入任意URL或远程服务。

工具名使用external__provider__tool。双下划线命名空间兼容当前OpenAI-compatible函数名约束；不会使用含点号的建议示例。禁止覆盖或部分覆盖现有名称。

首版只读白名单，拒绝有副作用工具。服务描述不授予权限；结果进入隔离的external_result，不变成TextEvidence/FigureEvidence、候选论文或引用白名单。

Schema映射为严格Pydantic模型：object/string/integer/number/boolean/array、必填、字符串枚举和基本长度/数值边界；所有object必须additionalProperties=false。拒绝未支持的$ref/组合/format/pattern/default等特性，避免静默放宽。

每次发现/调用均有同一截止时间覆盖锁、进程启动、管道读写和清理；有界输入输出、无自动重试、取消可终止并回收。每请求会话退出即close。仅验证WSL/Linux，不声称提供不受信任程序的OS沙箱或宿主机硬实时保证。

## 最小改动与生命周期

新增app/tools/integrations的models/schema_mapper/error_mapper/transport/adapter；tests/unit/tools/integrations及本地模拟服务、一个离线ReAct接入测试、架构说明。仅在非冻结react_runtime增加可选的受信任provider注入、注册和关闭钩子；不改策略、Registry、Executor、数据库、arXiv、UI、依赖锁。

未配置的provider保持禁用；程序化显式配置才允许连接本地服务。配置错误和发现错误应可观测且不使无关路径启动失败。不接收模型提供的路径、连接字符串或凭据。

## 测试矩阵与验收

离线覆盖：发现/原子注册、名称冲突、严格Schema/非法参数零调用、success/empty/error、未授权零调用、超时/取消/关闭、输入输出超限、非法协议/秘密脱敏、清理失败fail-closed、ReAct观察反馈/原多Agent选择与预算不变。

先执行新增测试，再Registry/Executor/ReAct/arXiv离线回归。完整测试用隔离环境显式清除真实授权门控和数据库URL、强制模型离线。记录JUnit、退出码、Ruff、formatter、git diff --check；真库/真实网络测试跳过不计通过。

B2登记只更新旧验收已有conclusion/real_tool_acceptance/blockers字段；原JSON逐字节备份、旧失败报告与调用记录不改。正式用户决策见../b2-final-decision.json。

本计划已根据只读源码形成并向用户说明；后续只实施上述范围，不启动P2。
