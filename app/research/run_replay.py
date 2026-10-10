"""只读加载版本管理中的 G2/G2.1 案例，不接受任意文件路径。"""

import hashlib
import json
from pathlib import Path


class ReplayError(ValueError):
    """固定公开错误代码，不暴露本地目录或解析异常正文。"""


_CASES = {
    "g2": {
        "directory": "docs/demos/blip2-vs-flamingo",
        "title": "G2 · 默认 Worker 真实演示",
        "description": "包含协议失败和部分已审核事实；完整报告不能形成跨论文比较。",
    },
    "g2-1": {
        "directory": "docs/acceptance/stage_g2_1/results",
        "title": "G2.1 · 工具协议修复验证",
        "description": "单动作协议错误减少，但保留真实证据不足；不代表语义质量提高。",
    },
}


class ReplayRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def list_cases(self) -> list[dict]:
        return [
            {
                "case_id": identity,
                "title": case["title"],
                "description": case["description"],
            }
            for identity, case in _CASES.items()
        ]

    def load(self, case_id: str) -> dict:
        case = _CASES.get(case_id)
        if case is None:
            raise ReplayError("unknown_replay_case")
        directory = self.root / case["directory"]
        records, source_files = {}, []
        for name in (
            "execution-record.json",
            "research-report.json",
            "execution-summary.json",
        ):
            path = (directory / name).resolve()
            if not path.is_relative_to(self.root):
                raise ReplayError("invalid_replay_source")
            try:
                content = path.read_bytes()
                value = json.loads(content.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReplayError("replay_artifact_unavailable") from exc
            if not isinstance(value, dict):
                raise ReplayError("invalid_replay_artifact")
            records[name] = value
            source_files.append(
                {
                    "path": f"{case['directory']}/{name}",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        execution = records["execution-record.json"]
        # 仅适配公开文件的组织方式，正文与中间记录保持原值。
        execution["outcome"] = {"report": records["research-report.json"]}
        execution["execution_summary"] = records["execution-summary.json"]
        # G2.1 紧凑轨迹缺少顶层请求，元数据从同次权威报告/摘要读取，不使用默认示例补齐。
        report = records["research-report.json"]
        execution.setdefault("question", report.get("question", ""))
        execution.setdefault("paper_ids", report.get("paper_ids", []))
        execution.setdefault("status", records["execution-summary.json"].get("status"))
        execution["replay_case_id"] = case_id
        execution["source"] = "replay"
        execution["replay_provenance"] = {
            "case_id": case_id,
            "source_files": source_files,
            "note": "历史文件只读回放；节点状态由实际结果派生，不构造实时调用时间线。",
        }
        return execution
