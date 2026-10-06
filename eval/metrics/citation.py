"""Identity and gold-ID relevance are distinct from semantic citation correctness."""


def citation_identity_valid(citations, provided_ids, corpus):
    items = {e.evidence_id: e for e in (*corpus.text_evidence, *corpus.figure_evidence)}
    for citation in citations:
        e = items.get(citation.evidence_id)
        if e is None or citation.evidence_id not in set(provided_ids):
            return False
        if (citation.paper_id, citation.page, citation.figure_id) != (
            e.paper_id,
            e.page,
            getattr(e, "figure_id", None),
        ):
            return False
    return True


def citation_relevance_precision(citations, relevant_ids):
    ids = {c.evidence_id for c in citations}
    if not ids or not relevant_ids:
        return None
    return len(ids & set(relevant_ids)) / len(ids)
