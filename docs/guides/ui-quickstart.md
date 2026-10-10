# 多智能体研究工作台：安装、启动与使用

本指南对应 Stage G4.2 中文动态工作台。界面复用 G2.1 原默认 Worker、协议适配、Reviewer、Recovery 和 Synthesis，不启用 F3.1 Coverage Worker。旧 `app.main` 单问题应用保持不变。

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

左栏填写中文、英文或混合问题，论文勾选是可选项。列表来自实际 Prepared Corpus：CLIP、BLIP、Flamingo、BLIP-2、LLaVA，共 46 条文本证据、5 条图像证据；未将 25 篇 Paper Catalog 全部当成可研究正文。示例问题只预填，不自动提交。

- **不勾选**：自动研究模式。工作台使用当前可研究论文的元数据，通过一次结构化模型决策选出 2–3 篇相关论文，再进入原研究链。没有匹配论文时明确终止，不替换成默认组合，也不下载新论文。
- **勾选 2–3 篇**：限定研究范围，保留所选 ID，不自动换论文。
- **只勾选 1 篇**：沿用当前多论文协议，页面及 API 明确提示选择 2–3 篇或取消勾选进入自动模式，不偷偷追加比较对象。

有汉字的中文或混合问题默认先转换成英文研究问题；纯英文直通。原问题保留，转换不允许增加研究目标、删改比较对象或技术术语。高级选项中的原文直通模式用于排查转换问题，失败时不会静默换一个英文问题继续研究。

点击“开始真实研究”会启动真实 Planner → 原 Worker → 引用校验/Reviewer → 条件 Recovery → Synthesis。真实模型请求可能产生费用，模型审核通过不等于人工事实核验。执行期间锁定提交；服务器也拒绝相同问题/论文的活跃重复任务。

中栏初始不预画 Agent 流程。只有取得事件后才出现节点；Worker 按实际论文记录展示，完成顺序取决于执行记录。Recovery 只在存在真实执行记录时展示；引用验证没有独立生命周期时只展示派生结果。点击节点查看真实需求、工具调用、检索范围、审核和错误。

右栏将最终报告放在固定独立区域，默认展示中文，可切换英文原文。中文是原报告的展示翻译，不是重新研究或独立审核；原 Claim、引用、论文 ID、页码、审核与缺失状态保持原值。研究结束但中文尚未准备好时先展示原报告；中文转换失败也保留原报告和明确提示，不覆盖研究结果。

过程详情及引用证据分别查看真实记录。证据正文保留英文原文和 `report_snapshot`；`corpus_check=not_checked` 表示展示层未再次将历史快照与当前 Corpus 比较，不能读成已完成人工核验。失败仍展示真实终态与可用部分记录，不能将未审核 Worker 论断提升为确认结论。

每秒轮询任务与事件游标；刷新恢复同一 `run_id`，不重新提交。取消期间显示“等待执行收尾”，实际收尾后才进入 cancelled。失败保留事件、部分结果和真正生成的报告。

## 5. 不花费 API 的历史演示

在“历史真实案例”选择并点击“查看历史回放”：

| 案例 | 原始结果 |
| --- | --- |
| G2 | 29 条派生展示事件；Flamingo Worker 协议失败；5 个已审核论断、7 个缺失单元。 |
| G2.1 | 33 条派生展示事件；Worker 协议合法；4 个已审核论断、10 个缺失单元；Reviewer 通过 4、跳过 10。 |

回放不调用 Provider、不检索、不重写原报告。中文展示来自单独保存、绑定原报告 SHA256 的离线翻译；绑定或结构校验失败回退英文原文。页面明确标“历史回放”，次数和耗时均为原实验数据。回放 run.status=completed 仅表示文件已加载；历史失败、不足和比较数 0 均保留。这不是新的成功研究实验，也没有新增人工事实审核。

来源及缺失策略见 [回放来源](../demos/workbench-replay.md)，事件边界见 [工作台架构](../architecture/workbench.md)。

## 6. 预算和存储边界

同时运行 1 个真实任务、排队最多 2 个。总体模型物理请求最多 200，API 可降低到 1–200，包含输入转换、自动论文选择、Planner、各 Agent、VLM、报告转换及实际依赖重试。语言适配和自动选择分别有单次上限，不在循环内不断增加模型请求。原 Worker 局部预算继续生效，总体研究工具预算 64。420 秒等待超时触发取消和 drain，不是强杀线程的精确截止时间。

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
python -m scripts.check_chinese_workbench_ui --output-dir artifacts/chinese-workbench-ui-check
```

该检查使用历史材料与 Fake Provider，显式禁止真实 Provider 调用。模拟部分用于验证无勾选、中文转换、原默认研究链及动态页面，不作为真实研究质量指标。需要本机 Playwright Chromium；`--font-file` 只是截图选配已有字体，正常界面不依赖 Windows 字体路径。旧 `check_workbench_ui` 保存 G4.1 布局的验收规则，本阶段以新检查入口为准。

| 问题 | 排查 |
| --- | --- |
| 命令/依赖不可用 | 确认在源码根目录并激活虚拟环境，检查现有依赖组。 |
| 真实提交禁用 | 刷新就绪状态；检查 Corpus、索引、缓存和六项 Provider 配置，不打印 Key。 |
| 无法自动选择论文 | 查看选择节点；问题可能超出当前可研究论文，或结构化选择无效，不会退化为默认论文组合。 |
| 中文问题转换失败 | 保留原问题，检查转换状态；可以明确启用原文直通再提交，不自动改写用户目标。 |
| 中文报告不可用 | 查看转换提示并切换英文原文；不影响原审核结果。历史翻译绑定失败不会请求 Provider。 |
| 索引绑定不一致 | 核对 collection、Corpus snapshot、模型配置；启动器不会静默新建索引。 |
| 首次真实任务较慢 | 本地模型加载、Provider 等待、重试和 drain 会消耗时间。 |
| 重复 409 / 排队满 429 | 查看已有任务，等待终态，不用连续点击增加调用。 |
| 取消后仍收尾 | 已停止新调用，在途线程真实结束后才能释放资源。 |
| 刷新找不到任务 | 可能重启、到期或超过保留上限；重新加载历史案例。 |
| 报告不足/跳过/降级 | 保留实际研究边界，不通过 UI 补写技术事实。 |

本阶段使用 Fake Provider 验证新增适配与真实领域链、使用原始历史产物验证回放和浏览器展示，没有启动新的真实 Provider 研究。原基线见 [G4.1 验收](../acceptance/stage_g4_1/acceptance_report.md)，新增事件与语言约束见 [中文工作台说明](../architecture/chinese-dynamic-workbench.md)。
