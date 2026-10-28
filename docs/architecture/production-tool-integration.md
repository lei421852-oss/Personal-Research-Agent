# 生产工具接入适配层（P1）

日期：2026-10-28。状态：本地接入边界；真实生产工具服务尚未指定和验收。

## 1. 已有架构与接入位置

原有入口仍是 ReActWorkbenchRuntime → MainResearchAgent / BoundedToolAgent。模型决定是否直接回答、调用检索工具或调用 run_multi_agent_research；P1没有新增意图分类器或改变多Agent启用策略。

| 模块 | 真实职责 | P1处理 |
|---|---|---|
| app/tools/registry.py | 动态注册、名称唯一性、模型可见Schema | 冻结，原样复用 |
| app/tools/executor.py | 同步输入校验、调用、Observation校验、脱敏、Trace | 冻结，原样复用 |
| app/agent/loop.py | 观察反馈、单动作循环、finish与证据白名单 | 冻结，原样复用 |
| app/research/react_runtime.py | 异步工作台、同步循环桥接、GuardedTool预算、取消 | 只增加可选来源注册和生命周期钩子 |
| app/tools/integrations/ | 本地协议映射、严格Schema、权限、监督进程和错误收口 | 新增 |
| app/persistence/ | 既有Run/Event/Report写入协调 | 不改Schema、代码或事务，不由适配器直接调用 |

逻辑执行边界中的预算属于现有GuardedTool，而不是ToolExecutor新增的能力。Executor本身仍是同步函数；异步研究入口通过已有_offload管理同步执行，P1没有额外建立后台线程池。

~~~mermaid
flowchart TD
    U["用户问题"] --> M["原主Agent：运行时自主选择动作"]
    M --> R["ToolRegistry / 参数Schema"]
    R --> E["原ToolExecutor + GuardedTool共享预算"]
    E --> P["Adapter：显式启用 / 只读白名单"]
    P --> S["StdioToolClient：同一deadline / 有界管道 / 零自动重试"]
    S --> L["管理员指定的本地工具进程"]
    L --> S
    S --> O["既有ToolObservation / Trace"]
    O --> M
    M --> X["可选原多Agent研究链或finish"]
~~~

Registry和Executor支持动态同步工具，但不直接await异步客户端。P1选择与此契约相符的同步stdio适配，未强行引入另一套并发模型。

## 2. ADR：为何暂不引入MCP

依据当前仓库审计，没有可复用MCP实现、已安装MCP SDK或已指定的生产服务。P1使用**项目内部、版本1的单请求/单响应JSON over stdio**完成可验证边界。它不是MCP，不兼容MCP initialize、JSON-RPC、resources、prompts或SSE。

只使用标准库和已声明Pydantic；未新增依赖、锁文件或MCP客户端文件。将来明确服务及协议后，才单独评估客户端SDK、许可证、版本兼容和认证方案。

第一版工具名为 external__provider__tool，采用兼容现有OpenAI-compatible函数名的命名空间。provider与远端tool各为小写字母起始、仅小写字母/数字/下划线、最长20字符。既有名称冲突会在整个注册批次预检时拒绝，不能覆盖或只注册一半。

## 3. 显式配置

默认没有外部来源，应用不会发现、启动或连接任何服务。只有宿主程序传入 external_tool_sources，且该来源配置 enabled=True 时才发现工具。

ExternalServiceConfig：

| 字段 | 默认/边界 | 作用 |
|---|---|---|
| provider | 必填，受限标识符 | 命名空间 |
| enabled | False | 显式启用 |
| allowed_tools | 空tuple，最多32个，不可重复 | 管理员只读白名单 |
| permission_level | 只允许read_only | 首版拒绝有副作用工具 |
| timeout_seconds | 5秒，0.1–30秒 | 含等待、传输、映射与清理的单操作预算 |
| max_input_bytes | 65536，64–1048576 | 编码输入限制 |
| max_output_bytes | 65536，64–1048576 | 完整协议响应限制 |
| max_discovered_tools | 16，1–32 | 服务发现数量上限 |

可复用WorkbenchDependencies.environment()的独立环境字典，通过ExternalServiceConfig.from_environment读取：

~~~dotenv
AGENT_EXTERNAL_TOOLS_ENABLED=false
AGENT_EXTERNAL_TOOL_TIMEOUT_SECONDS=5
AGENT_EXTERNAL_TOOL_MAX_OUTPUT_BYTES=65536
~~~

enabled仅接受小写true/false。配置非法时返回固定错误，不回显原值。**仅修改这些环境变量不会自动启动服务**：命令和工具白名单仍须在受信任宿主中明确注入。没有环境变量允许模型提供任意URL、命令或凭据。

以下是仓库内本地模拟来源的实际装配方式，不是生产服务配置，也不会自行调用模型：

~~~python
import sys
from pathlib import Path

from app.tools.integrations.adapter import LocalStdioSource
from app.tools.integrations.models import ExternalServiceConfig

source = LocalStdioSource(
    ExternalServiceConfig(
        provider="fixture",
        enabled=True,
        allowed_tools=("echo",),
    ),
    (
        sys.executable,
        str(Path("tests/unit/tools/integrations/mock_service.py").resolve()),
        "normal",
    ),
)
# 嵌入既有宿主时：
# runtime = ReActWorkbenchRuntime(dependencies, external_tool_sources=(source,))
~~~

可执行文件必须是绝对路径，命令参数只由管理员代码提供。当前不提供生产认证配置，因为真实服务未确定；不得把密码或API Key放在命令、Schema、工具参数、日志中。本地进程只继承明确的运行环境白名单，不继承Provider或数据库凭据。它是**受信任程序接入边界，不是OS沙箱**，不能保证恶意本地程序无法自行读文件或联网。

## 4. stdio协议

每次list_tools或call_tool创建独立进程。stdin为一个UTF-8 JSON文档，写完即关闭；stdout只能有一个UTF-8 JSON响应，随后进程退出。stderr丢弃到DEVNULL，防止无限日志堵塞或泄密。

发现请求：

~~~json
{"version":1,"request_id":"由客户端生成","operation":"list_tools"}
~~~

成功响应：

~~~json
{
  "version":1,
  "request_id":"与请求一致",
  "ok":true,
  "tools":[{
    "name":"echo",
    "description":"服务对工具的说明",
    "permission":"read_only",
    "input_schema":{
      "type":"object",
      "properties":{"query":{"type":"string","minLength":1}},
      "required":["query"],
      "additionalProperties":false
    }
  }]
}
~~~

调用请求：

~~~json
{"version":1,"request_id":"由客户端生成","operation":"call_tool","name":"echo","arguments":{"query":"local-fixture-only"}}
~~~

成功/空结果响应：

~~~json
{"version":1,"request_id":"与请求一致","ok":true,"result":{"status":"success","data":{"query":"local-fixture-only"}}}
~~~

空结果必须显式使用status=empty，data只能为null或空列表。错误响应使用ok=false和已定义error_code；未知错误码、重复JSON key、错误request_id、额外协议字段或无效结果都会被拒绝，不伪造成功或empty。

Schema仅支持：

- 所有object必须additionalProperties=false；必填字段、可选省略语义保持一致。
- string/integer/number/boolean/array/object、字符串enum、长度与数值边界。
- 严格拒绝字符串到整数、布尔到整数等隐式转换；非有限数字拒绝。
- 最大嵌套深度4、节点128、单object属性32。保留服务端已验证的输入契约，不发布内部默认None。
- 未支持的$ref、组合Schema、format、pattern、default、nullable类型等拒绝接入，不静默删掉约束。
- 服务描述和Schema说明不作为系统指令；模型可见说明由适配器固定，参数结构保留，远端注释不发布。

## 5. 权限、结果与引用

服务定义无法给自己授权。只允许管理员白名单中的工具注册，且必须明确声明read_only；缺少只读声明视为可能有副作用。首版不支持写工具，也不自动重试任何操作。

服务结果放在payload.external_result中，并标记trust=untrusted_external_data、evidence_verified=false。外部返回的“evidence”“system”“permissions”等键只是不可信嵌套数据，不能提升为Observation根部的Evidence、论文候选、管理权限或合法引用。

因此，当前外部数据可帮助主Agent下一步判断；它不能直接替代经过准备/检索/审核的论文事实。仍需现有证据工具取得合法Evidence后再回答论文问题，或明确证据不足。P1未新增finish种类、放宽Citation验证或把模拟服务返回当论文事实。

## 6. 超时、取消与资源生命周期

- 一次操作只建立一个绝对deadline。锁等待、进程启动、非阻塞stdin/stdout、协议解析、Schema映射和结果规范化均检查同一预算，不重置第二份时间。
- 父进程用selector轮询；预留最多0.2秒且不超过配置时长1/4用于清理。无响应、慢读、不读取stdin、输出后不退出都不能无限阻塞。
- 每请求使用原运行取消Event；调用期间或排队时取消均停止后续执行。Runtime仍传播CancelledError，不伪装为insufficient_evidence。
- 每次发现/调用最多启动一个进程；automatic_retries始终0。不复用HTTP/Provider RetryPolicy。
- 进程使用独立会话/进程组；正常和异常路径均回收、关闭管道和selector。无法确认回收时返回cleanup_failed并阻止后续进程。
- close可重复调用；运行结束、启动后发生异常或取消都会在Runtime finally中关闭会话。关闭不确认时不能将任务标记成功。
- 单操作输出最多读到上限+1字节后终止；输入编码也有总量/深度限制。未知异常不回显命令、原始输出或堆栈。
- 验收环境为WSL/Linux。进程创建与OS调度不是硬实时；不保证宿主机挂起或恶意脱离进程组程序的强制隔离。

## 7. 错误分类与Trace

公共envelope仍仅有success/empty/error，issue仍使用原枚举。精确分类在payload.external_error_code中，避免另建不兼容协议。

| external_error_code | 原issue.code | 含义 |
|---|---|---|
| unknown_tool | unknown_tool | 目标不存在 |
| invalid_arguments/input_limit/unsupported_schema | invalid_arguments | 不执行非法输入或拒绝接入 |
| permission_denied | invalid_arguments | 未启用、未授权或有副作用 |
| unavailable/timeout | dependency_failure | 依赖不可用或预算耗尽 |
| cancelled/closed | internal_failure | 取消/关闭，不自动重试 |
| invalid_output/output_limit | internal_failure | 无效/超限输出 |
| cleanup_failed/name_conflict/internal_failure | internal_failure | 清理、注册或内部故障 |

现有Observation约束只允许dependency_failure带retryable=true。这只表示错误类型，不授权自动重试；本适配器始终零自动重试，主Agent仍受原预算约束。

Tool Trace保留名称、已脱敏参数、状态、耗时与原result_count。外部数据没有自动获得Evidence，因此该计数为0，不能当成“工具没返回任何数据”。单操作执行元数据另记录request_id、attempts、automatic_retries、output_bytes、elapsed_ms、stop_reason、PID/returncode/reaped；工作台执行快照的external_tools记录会话历史与关闭情况。

能力发现属于装配，不伪造模型工具调用，不消耗一次业务tool_calls。发现失败以external_tools_unavailable及具体external_error_code记录；核心工具可继续工作。模型调用依然由既有预算账本计算。

## 8. 本地复现

在已有虚拟环境及基础测试依赖准备好后，从仓库根目录运行：

~~~bash
.venv/bin/python -m scripts.smoke_external_tools --help
.venv/bin/python -m scripts.smoke_external_tools \
  --output-dir artifacts/my-p1-local-smoke
.venv/bin/python -m pytest -q \
  tests/unit/tools/integrations tests/test_external_tool_integrations.py
~~~

smoke默认执行7个本地场景：success、empty、invalid_input、unavailable、timeout、cancelled、output_limit。输出目录可配置，observations.json使用排他创建，重复运行请指定新目录，避免覆盖证据。该脚本仅启动测试目录中的本地模拟服务，不调用LLM、arXiv或数据库，不能用作真实服务验收。

完整离线测试前必须关闭B1/B2 arXiv及H2数据库测试门控、移除测试数据库URL。P1留存的受限入口和实际执行记录位于artifacts/p1-external-tools-2026-10-28；该目录按项目约定忽略，不提交运行缓存。

相关测试仍检验“外部工具可选”“主Agent读取Observation后继续决策”“多Agent路径仍由策略选择”“伪引用被拒绝”和取消传播。全部为确定性/Scripted验证，不声称真实模型自主选择已被重新验收。

## 9. 边界与后续决策

- 未连接真实生产服务，未部署，没有生产认证、远程HTTP/SSE/MCP兼容性保证。
- 不使用Redis、记忆、数据库迁移或新表，不改变既有研究策略。
- 外部服务必须明确后，再审其协议、身份认证、权限、数据合法性、超时与成本；不能直接把stdio命令替换为未经审核的远程连接。
- 本地程序受信任；权限白名单不是网络/文件系统隔离。需要不信任程序隔离时应另行设计OS容器/权限边界。
- 字符串Schema子集有限，复杂MCP/JSON Schema需要另审；不以放宽验证解决兼容问题。
- 本地成功只能证明wiring及工程边界，不代表研究质量、长期可用性或真实工具事实正确性。
- P1结束即停，不启动P2记忆系统或真实端到端研究。

验收决策：[B2正式决策](../acceptance/b2-final-decision.json)。
实施前审计：[P1审计与ADR](../acceptance/p1/audit-and-plan.md)。
