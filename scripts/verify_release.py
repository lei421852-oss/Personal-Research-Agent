"""只读发布验收：保留原冻结校验，单独验证经用户授权的 README 文档例外。"""

import hashlib
import json
import subprocess
from pathlib import Path

from scripts.verify_worker_coverage_baseline import verify

ROOT = Path(__file__).resolve().parents[1]


def main():
    original = verify(ROOT / "docs/acceptance/stage_f3_1/baseline.json")
    freeze = json.loads(
        (ROOT / "docs/acceptance/stage_e_freeze/freeze_manifest.json").read_text()
    )
    exception = json.loads(
        (ROOT / "docs/releases/verification/documentation_exception.json").read_text()
    )
    expected = next(
        item["sha256"] for item in freeze["frozen_files"] if item["path"] == "README.md"
    )
    historical = subprocess.run(
        ["git", "show", "stage-e-freeze-v1:README.md"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    approved = (
        exception["path"] == "README.md"
        and exception["authorization"] == "explicit_user_approval_2026-10-11"
        and exception["historical_sha256"] == expected
        and historical.returncode == 0
        and hashlib.sha256(historical.stdout).hexdigest() == expected
        and hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest()
        == exception["release_sha256"]
    )
    unapproved = [path for path in original["frozen_mismatches"] if path != "README.md"]
    passed = (
        approved
        and not unapproved
        and not original["protected_artifact_mismatches"]
        and not original["frozen_manifest_root_changed"]
    )
    result = {
        "status": "PASS" if passed else "FAIL",
        "stage_e_files_checked": original["frozen_files_checked"],
        "unchanged_frozen_files": original["frozen_files_checked"]
        - len(original["frozen_mismatches"]),
        "approved_documentation_exceptions": ["README.md"] if approved else [],
        "historical_readme_verified_against_original_tag": approved,
        "unapproved_frozen_mismatches": unapproved,
        "protected_artifacts_checked": original["protected_artifacts_checked"],
        "protected_artifact_mismatches": original["protected_artifact_mismatches"],
        "original_verifier": original,
        "provider_calls": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
