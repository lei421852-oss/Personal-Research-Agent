"""Measured wall time and request counts only; no inferred token or dollar prices."""


def cost_metrics(latency_ms, llm_events, vlm_events, traces):
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
