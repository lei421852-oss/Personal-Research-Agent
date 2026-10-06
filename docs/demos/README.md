# Demo 记录与展示边界

这些 JSON 是既有运行产物的字段白名单摘录，不是重新执行或改写的成功案例。保留原问题、答案、引用、动作参数、状态和计数；省略原始模型请求、隐藏推理、凭据和本机路径。每份包含原文件名与 SHA-256，供本地原始记录核查。

| 文件 | 可验证内容 | 不能据此声称 |
| --- | --- | --- |
| [clip-text-real.json](clip-text-real.json) | 真实模型在一次 dev-text-01 中选择 CLIP scope、搜索后 finish；1 工具 / 2 模型调用 | 真实 Embedding 的检索质量、普遍的一步停止成功率 |
| [blip-fixed-vlm-real.json](blip-fixed-vlm-real.json) | 真实 VLM 收到 Figure + caption + 页面证据，固定三工具流程完成 | 动作由 Agent 自主选择，或 Demo B 原问题已经运行 |
| [blip-false-premise-real.json](blip-false-premise-real.json) | 历史真实 Agent 对中文错误前提问题调用视觉分析并纠正前提；6 工具 / 7 模型调用 | 最新 policy 下同样路径一定复现，或过程没有范围漂移 |
| [clip-wrong-scope-real.json](clip-wrong-scope-real.json) | 真实模型未指定 scope，重复得到 BLIP Evidence，最终没有满足 CLIP 问题 | Vector Store 出错，或 completed 等于任务成功 |

真实模型指模型调用实际发生；CLIP 与 Agent 记录的检索后端仍是 day5-controlled 固定证据，不是语义嵌入排序。固定 VLM 流程不执行 search_text。历史行为不能替代最新版本的统计评测。

## 三个作品集 Demo 的当前状态

- **A · CLIP Text QA：Real verified（单次行为记录）**，原问题和所需路径有历史真实记录。
- **B · BLIP CapFilt 解释：Documented only（该原问题待运行）**。相关固定 Figure 分析有真实记录，确切问题 `Explain Figure 3 of BLIP and its role in the CapFilt pipeline.` 尚无对应真实执行记录。
- **C · False Premise：Real verified（中文等价问题的历史记录）**。英文字面问题作为演示提示，不声称该字面问题已执行。历史路径包含额外文本搜索，保留原样。

现有 `python -m scripts.demo_day5` 和 `python -m eval.smoke` 是 **Scripted/Fake verified** 的执行链验证；不能用于真实性能或模型理解质量结论。

[UI 截图](ui-figure-fake.png)为 2026-10-07 浏览器检查生成的 Scripted/Fake 展示，不能作为真实模型回答或性能证据。字体仅在临时环境引用，未加入仓库。
