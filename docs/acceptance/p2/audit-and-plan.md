# P2 审计与实施决策

日期：2026-10-29。基线：32465578d27df95b896817240309f782ab2b9312。

## 审计事实

- 已有DatabaseResource提供应用级AsyncEngine、async_sessionmaker、短Session和取消清理；继续复用，不新建引擎体系。
- ResearchRepository已有Run/Event/Report事务，但没有可靠user/project身份表，也没有长期记忆语义；不复用日志/报告内容字段存记忆。
- H2 Base.metadata及测试明确约束三张原表。新MemoryBase单独管理一张记忆表，迁移环境组合两份metadata，避免污染原三表契约。
- 仅有h2_0001迁移；ResearchRepository没有硬编码head校验。新增线性p2_0001，不改旧快照、SCHEMA_VERSION或EXPECTED_ALEMBIC_REVISION。
- ToolRegistry/Executor同步；通过现有AsyncDecisionBridge让同步记忆工具等待异步Service/Repository，不使用asyncio.run或新的线程池。
- 无可靠用户身份：首版仅支持管理员指定项目scope。模型不能传scope_id，不建设跨用户共享记忆。
- MainFinishAction原五种答案类型不包含项目记忆；读取记忆后直接general会触发已有保护。需非冻结、可选的memory finish适配，不能放宽原论文引用。

## 数据与事务契约

单表agent_memories：memory_id、scope_type/project scope_id、memory_type、稳定memory_key、content、tags、source_type/source_ref/source_excerpt、状态、版本、内容哈希、创建/更新/过期时间、supersedes_id及删除来源。类型限preference/project_decision/project_fact/workflow_lesson/research_context。

同逻辑键同内容幂等；不同内容返回conflict，不静默覆盖。显式update/supersede创建新版本，旧正文不改；soft delete保留审核信息。正常检索只返回active且未过期记录。

同一scope/key用有界数据库事务锁与行锁序列化，部分唯一索引保证一个active版本，版本与替代关系有约束。事务不跨LLM/检索；提交确认未知返回memory_commit_unknown和reconcile_only，不包装为empty，不自动写重试。

首版来源仅接受当前原始用户问题中的原文片段；由服务绑定来源hash/ref，模型不能提供任意文件路径或冒充论文证据。记忆正文必须是来源原文片段，不把模型推测/自动摘要标为事实。秘密、明显敏感个人信息和隐藏推理不保存。

关键词/标签/类型结构化筛选，默认Top5、最多10；字面匹配、稳定排序，中文优先显式短关键词/标签，无Embedding或新外部服务。

## 最小接入方案

新增app/memory、独立memory ORM/Repository、p2_0001迁移、测试和文档。仅修改迁移env组合metadata、workbench显式启用装配、react_runtime可选绑定，不改Registry/Executor/BoundedToolAgent/MAIN_POLICY或原研究策略。

memory_search/save/update/forget均走原工具路径。每请求记忆范围和来源不可由模型改变，修改/删除仅针对本次已观察Memory ID。原模型/工具预算继续生效。

启用时用新文件中的finish/消息适配器声明可选memory答案类型，校验observed memory_ids并展示独立来源；原5种答案仍交给原验证器，Evidence/Citation白名单不扩大。无自动memory-first流程。未解决的写入故障不能把任务交付成成功。

默认禁用，不导入SQLAlchemy/asyncpg，不影响回放。启用需数据库和明确项目ID，不启动自动迁移。

## 测试和授权

先单元/Scripted/旧相关回归，再独立PostgreSQL迁移与事务测试，最后完整离线回归及Ruff/formatter/diff检查。原始B2/P1与13个未跟踪文件哈希保护。

用户已明确授权一次性postgres:16-alpine容器，仅127.0.0.1:55437，personal_agent_memory_test/p2_test，随机密码，无旧数据挂载；仅本库迁移/记忆测试，结束销毁。新门控P2_TEST_MEMORY_AUTHORIZED与P2_TEST_MEMORY_DATABASE_URL，校验准确目标，不接受agent_learning或生产库。

用户已确认覆盖模板旧条款：完成后push feature/agent-memory到research-agent（Personal-Research-Agent），由用户合并。不push origin（另一个仓库），不合并、不force，不自动进入下一阶段。
