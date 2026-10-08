"""只做 PDF 来源与语料投影校验，不初始化模型或调用 Provider。"""

import argparse
import json
from pathlib import Path

from app.evidence.corpus import load_paper_corpus
from app.research.corpus_validation import corpus_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers-dir", type=Path, default=Path("data/papers"))
    parser.add_argument(
        "--additional-papers-dir", type=Path, default=Path("data/research_papers")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        corpus = load_paper_corpus(
            args.papers_dir, additional_papers_dir=args.additional_papers_dir
        )
        report = corpus_snapshot(
            args.papers_dir, corpus, args.additional_papers_dir
        ) | {"status": "valid", "models_initialized": 0}
    except (ValueError, OSError) as exc:
        report = {
            "status": "invalid",
            "issue_code": type(exc).__name__,
            "corpus_validity_rate": None,
            "evidence_provenance_validity_rate": None,
            "indexing_allowed": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        raise SystemExit(1) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
