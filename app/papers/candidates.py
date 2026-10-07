"""候选只按 paper_id 取并集；首次出现顺序不表示跨查询融合排名。"""


def paper_searches(observations):
    return [
        {
            "query": o.payload.get("query"),
            "top_k": o.payload.get("top_k"),
            "papers": o.payload.get("papers", []),
        }
        for o in observations
        if o.tool_name == "search_papers" and o.status in {"success", "empty"}
    ]


def union_candidates(observations):
    candidates = {}
    for search in paper_searches(observations):
        for rank, paper in enumerate(search["papers"], 1):
            identity = paper["paper_id"]
            if identity not in candidates:
                candidates[identity] = dict(paper) | {"retrieved_by": []}
            candidates[identity]["retrieved_by"].append(
                {"query": search["query"], "rank": rank, "score": paper["score"]}
            )
    return list(candidates.values())
