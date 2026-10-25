# B1：真实 arXiv 论文检索工具

本工具只检索公开预印本元数据，不下载 PDF、不生成正文 Evidence、不自动扩充本地论文库。现有 `search_papers` 继续检索本地论文索引，工具职责各自明确。主 Agent 自主选择是否调用；普通问题不强制调用外部工具，也不默认升级多 Agent。

## 1. 配置与启用

沿用项目既有 Python 环境与安装方式，无新增依赖。客户端使用 Python 标准库，不需要 arXiv API Key；LLM Provider 配置仍只用于真实 Agent 决策，本工具独立 smoke 不调用 LLM。

```bash
export AGENT_ARXIV_ENABLED=yes
export AGENT_ARXIV_TIMEOUT_SECONDS=15
```

在启动应用前将变量注入进程环境；真实 WorkbenchDependencies 也会按既有方式合并 `.env` 和进程环境。不要把任何 Provider 密钥写入命令示例、工具参数或 Git。冻结 `.env.example` 本轮不修改。

- `AGENT_ARXIV_ENABLED`：默认 false；yes/true/1 启用，no/false/0/空值禁用。
- `AGENT_ARXIV_TIMEOUT_SECONDS`：默认 15，允许有限数值 1～30。禁用时忽略此项的错误值；启用但配置错误时，工具返回 unavailable/configuration_invalid，不阻断无关启动或普通回答。
- 配置首次使用时快照，并在同一运行时复用客户端；修改配置后重启应用。
- 端点固定为官方 `https://export.arxiv.org/api/query`；不接受用户或模型传入任意 URL，不跟随 HTTP 跳转，不发送鉴权头。
- 同一运行时单连接串行调用，开始间隔至少三秒。其他进程或机器的调用仍需由使用者合计遵守官方限流，本轮没有分布式协调。

## 2. 输入 Schema

工具名：`search_arxiv`。工具描述、JSON Schema 通过现有 ToolRegistry 交给模型；执行前由现有 ToolExecutor 校验。

```json
{"query": "ti:\"vision language\"", "top_k": 5}
```

`query` 是 arXiv 查询表达式，严格字符串，去外侧空白后 1～1000 字符，禁止控制字符。支持 `ti:`、`all:` 等官方语法，不自动编造论文名、具体方法或固定检索范围。`top_k` 是严格整数，默认 5，范围 1～10；bool、数字字符串、未知字段、缺失 query 均拒绝，参数拒绝不发 HTTP。

## 3. 输出与证据边界

沿用冻结的 `ToolObservation(status, tool_name, summary, payload, issue)`，不另建注册层或全局状态。每项论文包含：

- `paper_id`、原始 `arxiv_id`、标题、规范化官方 `source_url`。
- 可得摘要、作者、来源发布日期/年份、分类、可得 DOI 与 journal_reference。
- `abstract_truncated`，摘要缺失为 null，超过 4000 字符明确截断，不用模型补写。
- `provider=arxiv`、`local_paper_id`、`paper_discovered`、`paper_evidence_available`。

响应保留 `response_sha256`、真实 `retrieved_at`、本次 query/top_k。arXiv 没有返回相似度分数，因此不伪造 score；上游排序保留，按原始 ID 去重。

只有官方来源标识符及版本与本地目录**完全一致且唯一**时，才能关联原本地 paper_id；这只是身份关联，不是 Controller 替模型选择研究对象。其余采用 `arxiv:<原始标识符>`，不能把不同版本静默对应到旧 Prepared Evidence。

元数据候选可以进入 metadata 类型回答，不可用作正文技术事实的 Evidence Citation。外部候选与原观察消息一起反馈给模型；研究交接仍先通过原本地目录、来源、显式范围与引用校验。未知外部论文不能绕过它们启动研究。没有 Prepared Evidence 时保持 false，不下载、不解析、不虚构 evidence_id。

上游数据与模型解释分开：工具只返回校验后的元数据，原始工具/论文文本被现有策略视为数据。注册或成功返回元数据不意味着论文结论已通过 Reviewer。

## 4. 错误分类

| 语义 | 兼容结果 | 后续行为 |
|---|---|---|
| success | success，papers 非空，issue=null | 模型决定继续、研究或结束 |
| empty | empty + empty_result，papers=[] | 合法零结果，不是依赖失败 |
| invalid_input | error + invalid_arguments | 修正参数/查询，不能原样无条件重试 |
| timeout | error + dependency_failure；payload.error_category=timeout | 已有预算内由模型决定，不自动重试 |
| unavailable | error + dependency_failure；payload.error_category=unavailable | 网络、限流、HTTP 或配置不可用 |
| error | error + internal_failure；payload.error_category=error | 非法上游结构或其他已分类错误 |

参数校验错误由现有 Executor 产生，不强求它具有新工具的 error_category 字段；依 `issue.code=invalid_arguments` 判断即可。其他常见 `payload.error_code`：arxiv_timeout、network_unavailable、configuration_invalid、upstream_http_429、upstream_http_503、upstream_query_error、invalid_upstream_response、response_too_large。

HTTP 200 也可能返回 arXiv 错误条目，必须识别为错误。缺失结果计数、非法 XML/ID/关键字段、伪造来源、超限响应不能伪装成空结果或有效论文。响应大小上限 1 MiB，拒绝 DTD/实体声明和含 NUL 的非预期 XML；错误不公开原始异常、响应正文、路径或凭据。

B1 只有基础有限连接/读取等待、串行间隔和既有工具预算。没有自动 Retry、通用错误恢复或硬中止在途网络线程。socket 超时不等于对缓慢持续传输的完整绝对墙钟期限，完整超时/恢复闭环留给 B2；不能把这一边界描述成已完成。

## 5. 离线验证

在仓库根目录、已激活现有虚拟环境执行：

```bash
python -m pytest -q tests/unit/papers/test_arxiv.py tests/test_external_discovery.py
python -m pytest -q tests/test_react_workbench.py tests/test_main_policy_recovery.py tests/unit/papers/test_discovery.py tests/unit/tools
python -m scripts.smoke_arxiv_search --help
```

离线测试覆盖成功、无结果、非法参数、网络/超时/HTTP/结构错误、去重、源身份、资源释放、无凭据泄漏、Observation 回传、原文本问答和按需多 Agent。它们使用 Fake HTTP / Scripted Policy，不能作为真实模型决策或检索质量证据。

## 6. 真实工具验收与单次 smoke

```bash
B1_TEST_ARXIV_AUTHORIZED=yes python -m pytest -q tests/integration/test_arxiv_live.py
B1_TEST_ARXIV_AUTHORIZED=yes python -m scripts.smoke_arxiv_search \
  --query 'ti:"Learning Transferable Visual Models From Natural Language Supervision"' \
  --top-k 1
```

真实测试默认跳过，显式授权后执行两个真实元数据请求：合法题名请求与无匹配请求。可以设置 `B1_TEST_ARXIV_OUTPUT_DIR` 为已存在目录，导出实际 Observation/Trace；导出文件用排他创建避免覆盖。此门控不同于工作台的 AGENT_ARXIV_ENABLED，不自动启用真实模型。

CLI 可用 `--output` 指定新文件，父目录须已存在；不覆盖旧结果。非法参数例：传入 top_k=0 时返回 invalid_arguments 且不发送请求；其余详细边界由离线测试验证。服务不可用示例来自确定性故障夹具，不能标成真实 arXiv 故障。

本轮真实成功及无结果各一次已验证；未主动制造真实服务宕机或限流，也未调用真实 LLM 检查是否自主选中该工具。真实工具可用不代表未来每次网络调用都会成功，不等于生产验收。

## 7. 官方来源

- [arXiv API 用户手册](https://info.arxiv.org/help/api/user-manual.html)：请求参数、Atom 元数据与错误条目。
- [arXiv API 使用条款](https://info.arxiv.org/help/api/tou.html)：单连接和三秒间隔要求。

完成 B1 后停止，不自动进入 B2、长期记忆或多实例调度。
