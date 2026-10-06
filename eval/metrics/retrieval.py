"""按明确的证据 ID 计算召回；没有正向标注时记为不适用，而不是零分。"""


def unique(ids):
    return list(dict.fromkeys(ids))


def evidence_recall(observed_ids, relevant_ids):
    gold = set(relevant_ids)
    return len(set(observed_ids) & gold) / len(gold) if gold else None


def recall_at_k(retrieved_ids, relevant_ids, k):
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    return evidence_recall(unique(retrieved_ids)[:k], relevant_ids)
