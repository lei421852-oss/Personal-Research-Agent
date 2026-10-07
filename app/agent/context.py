"""从现有状态派生模型可见的决策上下文，不另建状态，也不代替模型判断证据充分性。"""

from app.agent.query_guard import approved_queries, expansion_calls_used
from app.papers.candidates import paper_searches, union_candidates
from app.papers.expansion import MAX_EXPANDED_QUERIES, MAX_EXPANSION_CALLS
from app.tools.redaction import public_value

EVIDENCE_SUMMARY_CHARS = 400


def build_decision_context(state, resolver):
    """整理模型下一轮需要的事实；摘要仅用于导航，完整证据仍保留在消息历史。"""
    evidence = []
    # 排序使上下文输出稳定，避免 set 的遍历顺序影响阅读和测试。
    for identity in sorted(state.observed_evidence_ids):
        item = resolver.resolve(identity)
        evidence.append(
            {
                "evidence_id": identity,
                "paper_id": item.paper_id,
                "page": item.page,
                "summary": item.text[:EVIDENCE_SUMMARY_CHARS],
            }
        )
    return public_value(
        {
            "question": state.question,
            "available_papers": resolver.paper_catalog,
            "observed_evidence": evidence,
            "discovered_papers": union_candidates(state.observations),
            "paper_searches": paper_searches(state.observations),
            "query_expansion": {
                "original_query": state.question,
                "calls_used": expansion_calls_used(state),
                "calls_remaining": MAX_EXPANSION_CALLS - expansion_calls_used(state),
                "max_expanded_queries": MAX_EXPANDED_QUERIES,
                "approved_queries": approved_queries(state),
            },
            "previous_actions": state.model_actions,
            "previous_failures": [
                o.model_dump(mode="json")
                for o in state.observations
                if o.status != "success"
            ],
            "budget_used": state.tool_calls_used,
            "budget_remaining": state.max_tool_calls - state.tool_calls_used,
            "decision_steps_used": state.model_calls_used,
            # 工具最多六次，模型另有一次 finish 机会；拦截动作也消耗决策次数。
            "decision_steps_remaining": max(
                0, state.max_tool_calls + 1 - state.model_calls_used
            ),
        }
    )
