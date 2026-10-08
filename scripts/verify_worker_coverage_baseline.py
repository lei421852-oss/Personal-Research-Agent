"""核验 Stage E 与 F2 原始文件；不重写冻结清单或评测结果。"""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(baseline_path):
    baseline = json.loads(baseline_path.read_text())
    freeze = json.loads(
        (ROOT / "docs/acceptance/stage_e_freeze/freeze_manifest.json").read_text()
    )

    def mismatches(entries):
        return [
            entry["path"]
            for entry in entries
            if not (ROOT / entry["path"]).is_file()
            or hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest()
            != entry["sha256"]
        ]

    frozen = mismatches(freeze["frozen_files"])
    protected = mismatches(baseline["protected_artifacts"])
    manifest_changed = (
        freeze["fingerprint_root_sha256"] != baseline["frozen_fingerprint_root_sha256"]
    )
    return {
        "status": "FAIL" if frozen or protected or manifest_changed else "PASS",
        "frozen_files_checked": len(freeze["frozen_files"]),
        "protected_artifacts_checked": len(baseline["protected_artifacts"]),
        "frozen_mismatches": frozen,
        "protected_artifact_mismatches": protected,
        "frozen_manifest_root_changed": manifest_changed,
        "provider_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "docs/acceptance/stage_f3_1/baseline.json",
    )
    args = parser.parse_args()
    result = verify(args.baseline)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
