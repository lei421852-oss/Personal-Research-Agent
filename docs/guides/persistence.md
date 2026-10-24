# H2.2阶段1：可选数据库基础设施

本阶段只提供PostgreSQL连接配置、AsyncEngine、Session工厂和连接健康检查。**研究任务、事件和报告仍保存在原内存管理器中**；数据库健康不表示这些内容已持久化。本阶段没有业务ORM、Repository、Alembic或自动恢复。

## 安装与依赖锁

推荐Python3.12、Linux/WSL，在源码根目录操作。基础锁包含现有api/llm extras，数据库锁包含SQLAlchemy[asyncio]/asyncpg；测试锁另包含原data extra声明的PyMuPDF，供既有Prepared PDF测试使用。不自动安装Torch、下载模型或建立索引。

```bash
python3.12 -m venv artifacts/h2-stage1/dev-env
source artifacts/h2-stage1/dev-env/bin/activate
python -m pip install --require-hashes -r requirements-runtime.lock -r requirements-persistence.lock -r requirements-h2-test.lock
python -m pip install --no-deps -e .
python -m pip check
```

仅运行时省略requirements-h2-test.lock，安装源码可用`python -m pip install --no-deps .`。项目仍要求源码checkout；wheel不是完整论文/configs/工作台资源分发包。运行锁不包含构建后端、系统包或模型缓存，相关工具链需另行记录。

默认禁用数据库时只需基础运行锁，SQLAlchemy/asyncpg不必安装：

```bash
python -m pip install --require-hashes -r requirements-runtime.lock
python -m pip install --no-deps -e .
```

维护者在独立工具环境生成锁，输入范围在requirements-persistence.txt和requirements-h2-test.txt中，冻结pyproject不变：

```bash
python -m pip install 'pip-tools==7.6.2'
python -m piptools compile --extra api --extra llm --generate-hashes --no-emit-index-url --no-emit-trusted-host --output-file requirements-runtime.lock pyproject.toml
python -m piptools compile --generate-hashes --no-emit-index-url --no-emit-trusted-host --output-file requirements-persistence.lock requirements-persistence.txt
python -m piptools compile --generate-hashes --no-emit-index-url --no-emit-trusted-host --output-file requirements-h2-test.lock requirements-h2-test.txt
```

提交锁文件前核对没有个人包源、凭据或本地绝对路径。相同输入优先复用已有锁，不自动upgrade；版本更新必须重新安装和回归。跨平台或其他Python版本应重新解析验证。

## 配置

默认`H2_DATABASE_ENABLED=false`，不会读取其余H2配置、导入数据库驱动或创建连接。数据库设置只读取进程环境或create_app显式注入值，**不读取项目.env**。现有Provider的.env读取方式没有改变。

启用时由部署者安全注入`H2_DATABASE_ENABLED=true`与`H2_DATABASE_URL`，不要把真实连接串放进文档、命令历史、截图或Git。驱动必须是postgresql+asyncpg，明确提供host、database、username、password，端口默认5432；特殊字符需正确URL编码。

允许的查询项只有`ssl=disable|require|verify-full`，缺省require；require是加密而非证书身份验证，verify-full还校验证书/主机名。disable只用于明确的本地临时环境。其他连接参数、多个ssl值、多host/Unix socket和自定义客户端证书不在第一版范围。

| 环境变量 | 默认 | 范围 |
|---|---:|---|
| H2_DATABASE_POOL_SIZE | 2 | 1–10 |
| H2_DATABASE_MAX_OVERFLOW | 0 | 0–10 |
| H2_DATABASE_POOL_TIMEOUT | 5秒 | 大于0且不超过60 |
| H2_DATABASE_CONNECT_TIMEOUT | 5秒 | 大于0且不超过60 |
| H2_DATABASE_COMMAND_TIMEOUT | 5秒 | 大于0且不超过60 |
| H2_DATABASE_HEALTH_TIMEOUT | 5秒 | 大于0且不超过60 |

固定pool_pre_ping=true、pool_recycle=1800秒、echo/echo_pool=false、hide_parameters=true。只使用SQLAlchemy异步池，不叠加asyncpg.create_pool；没有应用层自动重试。pre_ping能检查checkout时的失效连接，不保证事务中断可重放。

## 生命周期与Session

现有`python -m scripts.launch`入口保留。启用且配置有效时，在工作台lifespan中创建一个Engine和async_sessionmaker；创建本身不建立网络连接，不在startup自动探测目标。每个lifespan独立创建资源，不跨事件循环复用。

启动中途失败也会清理已创建资源；关闭按Manager.close、DatabaseResource.close顺序，Manager关闭异常不跳过数据库清理。关闭拒绝新Session，清理受管理Session后await engine.dispose。

`get_db_session`为需要数据库的消费者提供独立Session，expire_on_commit=false、autoflush=false，不自动commit；正常/异常退出rollback未提交事务并close，取消保持CancelledError。现有研究请求没有接入该依赖，不能把Session跨线程共享或持有至整个研究结束。

取消/关闭清理有5秒预算；超时返回storage_cleanup_failed，不能声称任意驱动或外部Session一定被强制清理。所有Session应通过本资源作用域创建。

## 独立连接健康检查

`GET /api/storage/health`：

- 默认禁用：200，status=disabled，scope=connection_only，不连接。
- 启用后的显式检查：一次SELECT 1，成功200/ready，仅表示本次连接可用。
- 连接/认证故障：503/storage_unavailable。
- 池等待超时：503/storage_pool_timeout；总体/命令超时：503/storage_health_timeout。
- 非有效lifespan或关闭中：503/storage_not_initialized或storage_closing。

响应和日志不包含URL、密码或驱动原始异常。启用配置错误/缺驱动会以固定安全code使startup失败；默认禁用不受影响。研究health与launch --check仍沿用原语义，**它们不会顺便连接PostgreSQL**。本阶段不把数据库故障当研究证据不足。

## 测试与授权边界

```bash
python -m pytest -q tests/unit/persistence
python -m pytest -q tests/integration/test_database_infrastructure_postgresql.py
python -m pytest -q tests/test_research_runs_api.py tests/test_research_replay.py tests/unit/research/test_workbench_dependencies.py
ruff check app tests scripts eval
ruff format --check app tests scripts eval
```

单元测试使用Fake连接或惰性构造，不需要PostgreSQL。禁用回放测试在子进程中主动阻止SQLAlchemy/asyncpg导入。

临时PostgreSQL集成测试只有维护者明确授权目标并设置`H2_TEST_DATABASE_AUTHORIZED=yes`及`H2_TEST_DATABASE_URL`才允许执行，否则skip。目标须为确认可丢弃的独立本地库，库名以personal_agent_test_开头，且与运行库不同；命名/本机地址不能代替人工授权。fixture不建库、不建表、不迁移，不回退运行URL。测试仅SELECT，包含错误rollback、pg_sleep超时、池耗尽和连接失效；不操作正式数据库或终止其backend。

完整旧套件仍需要原retrieval/vision/data依赖和历史资源。新环境仅最小套件通过不等于完整研究回归；缺历史索引要分别报告，而非更改测试预期或自动下载模型。

## 后续阶段及人工维护

本阶段不会自动判死、接管、重启或清理遗留研究任务，也没有幂等创建、调度窗口标记和报告版本表；这些保留在已审查的H2.1设计中。

后续若实现遗留任务维护，必须先停止新请求，确认全部实例/在途调用停止，只读列出run_id、owner、状态、版本、报告及事件高水位；维护者逐项授权后才按预期状态/版本短事务更新，读回核验并保留记录。未经授权不能执行DDL/DML；本指南没有维护写库CLI。

阶段1停止于基础设施验收。Schema核验、业务模型、迁移和持久化接入均须后续独立批准。
