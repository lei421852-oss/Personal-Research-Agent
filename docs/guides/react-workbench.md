# 问题驱动的研究工作台使用指南

默认入口已改为主 Agent：只需输入自然语言问题，不必选择论文或执行模式。主模型可以直接回答、检索后回答，或可选调用多论文研究链。

## 启动

在仓库根目录，使用已有项目环境；新环境的安装说明见 [development.md](development.md)。

```bash
source .venv/bin/activate
python -m scripts.launch --check
python -m scripts.launch
```

打开 `http://127.0.0.1:8000`。端口可用 `--port 8001` 修改。`--check` 是只读预检，不发送模型请求，不加载 Embedding，不重建索引。启动命令只创建服务，不自动研究。

`.env` 中已有 LLM 配置支持主决策与语言适配；实际图像分析需要 VLM 配置。变量名、Provider 地址配置和本地模型准备见 [development.md](development.md)。不得提交 `.env` 或凭据。

主 Agent 就绪不要求所有领域资源都就绪：一般解释可以不加载检索模型；真正调用论文发现/正文检索时才检查并装配对应资源。Paper Index 需现有增强画像缓存与索引一致，Prepared Evidence Index 需匹配管理员配置快照。依赖失败以工具观察反馈，不自动下载或重建。

## 操作与边界

直接输入问题并开始。左侧 25 篇论文目录只供浏览，正文已准备和仅元数据分别标记。默认没有外部实时搜索，没有固定目标论文组合。当前正文只有 CLIP、BLIP、Flamingo、BLIP-2、LLaVA；其余候选可被发现但不能产生无来源正文引用。

执行过程自动选择布局：主 Agent 直接回答或仅调用领域工具时，展示线性轨迹；实际调用 `run_multi_agent_research` 时切换为树状协作图。历史研究回放按其实际研究角色展示树状图。树中按论文展开 Worker、引用校验和 Reviewer，并展示实际出现的规划、恢复与综合角色；缺少论文归属的审核保留在公共分支，不猜测所属论文。树状连线表示职责归属，不表示具体执行时间或所有分支同时并发。

点击实际角色节点查看公开动作、参数、证据和失败。主 Agent 未启动多论文研究时不会出现 Worker；恢复节点只在实际恢复时出现。答案先显示直接回答，中文转换期间可先查看原文，完成后可切换语言。原论文证据与模型审核均不等于独立人工事实核验。

取消是软取消，会等待已进入同步依赖的请求收尾；刷新页面可恢复进程内任务。服务重启会丢失本次内存历史。顶部历史回放只读取 G2/G2.1 已有执行记录和中文副本，不调用模型，缺失状态仍保留。

## API

```bash
curl -X POST http://127.0.0.1:8000/api/research/runs \
  -H 'Content-Type: application/json' \
  -d '{"question":"Why does Flamingo use the Perceiver Resampler?"}'
```

返回 run_id 后查询 `/api/research/runs/{run_id}`、`/events?after=0` 和 `/report`；取消使用 POST `/cancel`。API 可选 `paper_ids` 允许 1–3 篇 Catalog 中论文，属于显式范围，UI 不要求输入。预算 `max_model_calls` 为 1–200，包含所有物理模型/VLM/语言请求；过小会导致无法完成。

## 本地工程验收

```bash
python -m pytest -q
ruff check app tests scripts eval
ruff format --check app tests scripts eval
python -m scripts.verify_worker_coverage_baseline
python -m scripts.check_react_workbench_ui --output-dir artifacts/react-workbench-ui-check
```

浏览器检查需 `ui-test` 依赖与已安装的 Playwright Chromium，可通过 `PLAYWRIGHT_BROWSERS_PATH` 指定现有缓存；可选 `--font-file` 为中文截图提供字体。检查运行 Scripted 主策略和真实历史回放，真实 Provider 调用为零，不用于论文质量评分。旧浏览器脚本保留旧布局验收规则，当前主入口使用新的脚本。

## 排查

- 无法开始：先看主 Agent / 语言模型配置；缺凭据仍可历史回放。
- 论文检索失败：检查既有 Paper Index、画像缓存来源及固定 Embedding 缓存；本轮不会自动修复索引。
- 论文被发现但无法回答：检查正文可用状态，元数据不能替代原论文证据。
- 未生成中文：原回答保留，检查语言转换错误及共享预算，不要把翻译失败归为研究事实错误。
- 多动作协议失败：主循环仍严格单动作，未执行批量中的第一项；停止结果和公开 trace 保留。
- 研究预算不足：研究工具不会开启新额度；降低目标范围需由用户或模型明确提出，程序不会静默替换对象。

完整设计见 [react-workbench.md](../architecture/react-workbench.md)，验收记录见 [stage_g4_2_react](../acceptance/stage_g4_2_react/acceptance_report.md)。
