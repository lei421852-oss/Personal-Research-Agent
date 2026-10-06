"""只统计实际耗时和调用次数，不推测 token 或费用。"""


def cost_metrics(latency_ms, llm_events, vlm_events, traces):
    # 冗余搜索仅记录拦截事实，没有执行工具；调用数和工具耗时均排除它。
    traces = [
        t for t in traces if not t.failure or t.failure.get("executed") is not False
    ]
    return {
        "latency_ms": latency_ms,
        "model_calls": len(llm_events),
        "vlm_calls": len(vlm_events),
        "tool_calls": len(traces),
        "llm_latency_ms": sum(e["latency_ms"] for e in llm_events),
        "vlm_latency_ms": sum(e["latency_ms"] for e in vlm_events),
        "tool_latency_ms": sum(t.latency_ms for t in traces),
    }
