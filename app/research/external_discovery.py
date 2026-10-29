"""只扩展外部候选观察账本，不修改冻结候选聚合器、引用或研究交接规则。"""

from app.research.research_handoff import ResearchContext


class ExternalDiscoveryContext(ResearchContext):
    def candidates(self):
        local = super().candidates()
        candidates = {paper["paper_id"]: dict(paper) for paper in local}
        for observation in self.observations:
            if (
                observation.tool_name != "search_arxiv"
                or observation.status != "success"
            ):
                continue
            for rank, paper in enumerate(observation.payload.get("papers", []), 1):
                identity = paper["paper_id"]
                if identity not in candidates:
                    candidates[identity] = dict(paper) | {"retrieved_by": []}
                candidates[identity].setdefault("retrieved_by", []).append(
                    {
                        "query": observation.payload["query"],
                        "rank": rank,
                        "provider": "arxiv",
                        "score": None,
                    }
                )
        return list(candidates.values())

    def handoff(self, arguments, budget):
        # 原目录、来源与显式范围校验必须先通过；这里只补真实外部查询的来源记录。
        handoff = super().handoff(arguments, budget)
        external_queries = [
            observation.payload["query"]
            for observation in self.observations
            if observation.tool_name == "search_arxiv"
            and observation.status == "success"
        ]
        return handoff.model_copy(
            update={
                "retrieval_queries": list(
                    dict.fromkeys([*handoff.retrieval_queries, *external_queries])
                )
            }
        )
