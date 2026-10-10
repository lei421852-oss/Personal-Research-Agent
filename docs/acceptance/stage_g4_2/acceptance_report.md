# Stage G4.2：最终发布与求职展示验收

**日期：2026-10-10。结论：PASS（工程与文档发布验收）。**

本阶段只整理发布入口、真实UI展示、技术亮点、简历/面试与版本证据，没有修改研究架构、提示词、Provider、Retriever、语料、索引或评测标签，没有重跑质量评测，也没有新真实Provider研究。

## 1. 实际交付与冻结处理

| 交付 | 文件 |
| --- | --- |
| 当前项目主页 | [docs/project/README.md](../../project/README.md)：介绍、真实截图、Mermaid、能力、启动、实验口径、限制与导航。 |
| 根发布入口 | [PROJECT.md](../../../PROJECT.md)：从仓库文件列表进入新主页。 |
| 文档导航 | [docs/README.md](../../README.md)：仅在顶部新增当前入口，原G1历史说明保留。 |
| 展示材料 | [showcase.md](../../project/showcase.md)、[截图清单](../../project/images/sources.json)、原样复制的G2.1整体图。 |
| 技术亮点 | [highlights.md](../../project/highlights.md)：五项问题/机制/实现证据/局限。 |
| 求职材料 | [resume-and-interview.md](../../project/resume-and-interview.md)：名称、技术栈、简介、4条核心成果、3岗位侧重、一分钟介绍、19技术问答。 |
| 验收证据 | 本目录工程日志、浏览器记录、链接/指标/敏感扫描与完整性清单。 |

根README属于Stage E 292个冻结文件，原哈希不变，没有直接覆盖、改名或绕过校验。GitHub仓库根部仍显示旧冻结README；用于求职的当前项目主页是docs/project/README.md，可由PROJECT.md/文档导航或直接链接进入。

## 2. UI展示与首用检查

三张展示图片均来源于G4.1实际页面的历史回放，source=replay。整体图与原PNG字节完全一致；Worker失败和Evidence图直接引用已提交原图片。没有绘图伪造应用、裁改研究内容或把历史模型次数算作本轮调用。

G4.2重新运行了现有浏览器验证入口，明确禁用真实研究。8项检查PASS：首次页面不自动提交；Provider不就绪时可回放；两案例Worker/审核/缺失状态正确；点击引用显示原论文页码/正文；刷新恢复不重复POST/事件；文本注入不执行；390px布局不溢出；无浏览器异常。详情见 [browser_check.json](browser_check.json)。

保留原结果：G2为29展示事件/5已审核/7缺失，G2.1为33事件/4已审核/10缺失、比较0。Validator无虚构时间，合法不足Reviewer显示skipped而非工程失败。

首用理解点已在展示文档说明：左侧文本框是下一次真实研究的输入，历史问题在中栏；验证截图中真实按钮禁用是显式禁止付费调用，并非API故障。未发现必须修改冻结代码或UI的展示阻断问题。

## 3. 亮点与实验核验

| 指标 | 本次使用的原口径 |
| --- | --- |
| F2 79/90 | 90条混合质量任务的AI参考接受，独立人审Gold为0，不是人审准确率。 |
| 比较8/15 | F2整题比较通过，不能与分项平均或结构性引用率混淆。 |
| F3.1-E严格4/7 | 已知失败、非盲AI诊断集；模型85→101、工具26→29，有成本和质量权衡，Coverage不默认启用。 |
| G2→G2.1错误4→0/8→0 | 同主题各一次真实记录，Planner计划不同；14合法终态含10不足，比较0，不推断研究质量改善。 |
| G4.1 1155测试 | 工程链、API和历史UI的证据，不是新的Provider研究或像素推理质量验证。 |

这些表述与 [G3实验汇总](../../reports/experiment-summary.md)、原始结果和当前代码一致。技术亮点区分引用身份/模型语义审核/人审、三种恢复和三类事件；没有宣称完全消除幻觉、保证研究正确或生产级可靠。

## 4. 本轮实际工程检查

| 检查 | 结果 |
| --- | --- |
| 完整pytest | **1155 passed，0 failed，24.36秒**；本轮不新增/删除测试。 |
| Ruff check app tests scripts eval | PASS，退出码0。 |
| Ruff format --check app tests scripts eval | 249 files already formatted，退出码0。 |
| scripts.launch --check | UI/replay/real配置准备条件通过，0付费探测。 |
| 现有冻结CLI | Stage E 292、原受保护产物47项通过，退出码0。 |
| 浏览器首用/真实历史回放 | 8项PASS，新Provider/检索/Embedding为0。 |
| 已跟踪源码测试 | 1143 passed，原12项未跟踪诊断测试未纳入发布；使用现有本地索引环境。 |
| 干净源码启动 | Git源码副本无.env/无索引，UI与历史回放可用，G2.1原4论断/10缺失保留，正常退出。 |
| 文档与指标 | 新文档相对链接、截图SHA及核心实验声明核对通过。 |
| 敏感信息 | 当前文本和HEAD可达历史启发式扫描未发现疑似生产凭据/私钥，无被跟踪.env、无GitHub100MiB大文件阻塞；安全测试固定假Key单独记录。 |

日志见 [engineering_checks.json](engineering_checks.json)、[测试](test_results.txt)、[静态](lint_results.txt)、[敏感扫描](sensitive_scan.json)、[文档审计](documentation_checks.json)。没有以旧G4.1测试报告代替本轮执行。一条既有Starlette/httpx弃用警告未造成失败，本阶段不改依赖。

可复现性补充见 [clean_source_check.json](clean_source_check.json) 与 [已跟踪测试日志](tracked_test_results.txt)。本机1155包含12项用户原未跟踪诊断；发布源码为1143项，某项索引集成检查依赖未提交的本地SQLite索引。干净源码只复用本机已有依赖、清除Provider配置并验证回放，没有宣称全新环境安装或缺索引也能跑完整测试。因发现这一口径差异，额外增加一次文档/验收补充提交，不改代码、测试或原产物。

## 5. 文件保护与Git发布

起点为feature/stage-g4-1-agent-workbench，HEAD 9e56ac22ca541c2dde0d2d8b656febc9b0d729f9；原已跟踪工作区干净，13个原未跟踪诊断保留。本地分支release/stage-g4-2-final-delivery。

582个原已跟踪文件中，581个内容不变，仅非冻结docs/README.md按要求新增导航；Stage E全部292不变，原未跟踪13及历史忽略产物40全部匹配。F2/F3.1/F3.1-E/G2/G2.1/G3/G4.1验收与原结果、标签、截图未变，见 [完整性检查](integrity_checks.json)。

提交顺序为项目主页/导航、UI展示/亮点、简历/面试、最终工程验收。全部通过后创建本地附注标签v0.1.0-research-agent；发现已有标签时不覆盖。最终commit/tag对象与推送结果由Git和最终交付记录核对，避免报告自引用未来SHA。Stage E原标签保持原对象。

用户随后明确要求推送到git@github.com:lei421852-oss/Personal-Research-Agent.git，此指令覆盖原计划仅本地Git限制。原origin指向另一仓库，保持不变；新增独立research-agent发布远程。首次只读检查新仓库为空，SSH访问通过。只在本轮本地验收与附注标签建立后，非强制推送main、发布分支和这一个新标签；不执行force或覆盖远程历史。见 [远程预检](remote_preflight.json)，实际推送回执在最终交付记录中报告。

## 6. 尚存限制与交付结论

- 研究主要基于Prepared文本与图注，没有独立人工事实审核或已验证像素推理结论。
- 复杂比较仍可不足；Coverage保持实验，G2.1历史比较0不作美化。
- 新UI未做新的付费端到端研究，ready只是配置/缓存/索引的只读条件。
- 工作台为单进程内存管理，重启丢任务、取消等待drain；原wheel未保证完整打包新入口。
- GitHub自动根README仍是历史冻结页，新主页以发布入口及直接文档链接展示。
- 敏感扫描是启发式检查，不声称穷尽所有潜在敏感值；没有打印秘密内容。

达到G4.2工程、文档和本地版本发布条件。完成指定推送后停止，不自动启动下一阶段开发、真实研究或新质量优化。
