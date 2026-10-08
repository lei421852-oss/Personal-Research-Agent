"""只读核验已保存的验收证据；不调用模型、不改写评测结果。"""

import argparse
import hashlib
import json
import math
from pathlib import Path

from app.evidence.corpus import load_paper_corpus
from app.evidence.resolver import EvidenceResolver
from app.research.corpus_validation import canonical_hash, corpus_snapshot
from app.research.models import ResearchPlan, WorkerResult
from app.research.recovery_models import RecoveryOutcome, result_ref
from app.research.requirements import expand_requirements, stable_id
from app.research.review_models import ReviewResult
from app.research.synthesis_input import SynthesisInputBuilder
from app.research.synthesis_models import ResearchReport
from eval.cross_paper import CrossPaperDataset, audit_report, research_metrics


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvidenceAudit:
    def __init__(self, root, directory):
        self.root, self.directory = root, directory
        self.checks, self.summaries, self.warnings = [], {}, []
        self.corpus = load_paper_corpus(
            root / "data/papers", additional_papers_dir=root / "data/research_papers"
        )
        self.resolver = EvidenceResolver(self.corpus)
        self.snapshot = corpus_snapshot(
            root / "data/papers", self.corpus, root / "data/research_papers"
        )
        self.dataset = CrossPaperDataset.model_validate_json(
            (root / "eval/datasets/stage_e.json").read_text()
        )
        self.dataset.validate_corpus(self.corpus, self.snapshot["snapshot_id"])

    def check(self, name, condition):
        self.checks.append({"check": name, "status": "PASS" if condition else "FAIL"})

    def read(self, suffix, date="2026-10-08"):
        return json.loads(
            (
                self.directory
                / "evidence"
                / f"{date}-多模态论文研究智能体-{suffix}.json"
            ).read_text()
        )

    def history(self, label, outcome):
        history = outcome.merged.history
        self.check(
            label + ":unique_history_ids",
            len({h.record_id for h in history}) == len(history),
        )
        self.check(
            label + ":at_most_one_recovery_round",
            outcome.metrics.recovery_rounds_used <= 1
            and all(h.attempt_index in {0, 1} for h in history),
        )
        for index, record in enumerate(history):
            value = (
                record.worker_result
                if record.kind == "worker"
                else record.review_result
            )
            ref = result_ref(record.kind, value)
            self.check(
                label + f":history_{index}_content_hash",
                record.result_ref == ref and record.status == value.status.value,
            )
            expected = stable_id(
                "history_",
                ref,
                str(record.attempt_index),
                str(index),
                record.recorded_at,
            )
            self.check(
                label + f":history_{index}_identity", record.record_id == expected
            )

    def rebuilt_source(self, label, plan, outcome):
        rebuilt = SynthesisInputBuilder(self.resolver).build(
            plan, outcome.merged.worker_results, outcome.merged.review_results
        )
        self.check(
            label + ":synthesis_input_rebuilt_from_reviews",
            rebuilt == outcome.synthesis_input,
        )
        audit = audit_report(rebuilt, outcome.report)
        self.check(label + ":report_provenance_and_comparisons", True)
        reviewed = {r.requirement_id: r for r in outcome.merged.review_results}
        requirements = {
            r.requirement_id: r
            for w in outcome.merged.worker_results
            for r in w.requirements
        }
        for claim in outcome.report.verified_claims:
            r, req = reviewed[claim.requirement_id], requirements[claim.requirement_id]
            self.check(
                label + ":verified_" + claim.requirement_id,
                r.status == "passed"
                and r.validation_status == "passed"
                and r.claim == claim.text == req.claim
                and r.evidence_ids == claim.evidence_ids == req.evidence_ids,
            )
        rejected = {
            h.review_result.claim
            for h in outcome.merged.history
            if h.kind == "review" and h.review_result.status == "rejected"
        }
        self.check(
            label + ":rejected_claim_bodies_excluded",
            not rejected.intersection(c.text for c in outcome.report.verified_claims),
        )
        self.history(label, outcome)
        return rebuilt, audit

    def stage_d(self):
        plan = self.dataset.original_plan
        for suffix, letter in [
            ("StageD真实RecoverySmokeA", "A"),
            ("StageD受控再次拒绝SmokeB", "B"),
            ("StageD无语料跳过SmokeC", "C"),
        ]:
            data = self.read(suffix)
            outcome = RecoveryOutcome.model_validate(data["outcome"])
            source, _ = self.rebuilt_source("stage_d_" + letter, plan, outcome)
            target = next((t.target_requirement_ids[0] for t in outcome.tasks), None)
            after = {c.requirement_id: c for c in source.cells}
            recovered = [h for h in outcome.merged.history if h.attempt_index == 1]
            worker_calls = sum(
                h.worker_result.trace.llm_calls for h in recovered if h.kind == "worker"
            )
            review_calls = sum(
                h.review_result.trace.model_calls
                for h in recovered
                if h.kind == "review"
            )
            models = (
                sum(
                    h.worker_result.trace.model_calls
                    for h in recovered
                    if h.kind == "worker"
                )
                + review_calls
                + outcome.report.metrics.synthesis_model_calls
            )
            tools = sum(
                h.worker_result.trace.tool_calls
                for h in recovered
                if h.kind == "worker"
            )
            self.check(
                "stage_d_" + letter + ":additional_costs",
                models == outcome.metrics.additional_model_calls
                and tools == outcome.metrics.additional_tool_calls,
            )
            if letter == "A":
                self.check(
                    "stage_d_A:controlled_initial_rejection_real_recovery",
                    data["initial_rejection_is_controlled"]
                    and data["real_provider_used"]
                    and worker_calls == review_calls == 1
                    and outcome.metrics.recovery_passed_count == 1
                    and after[target].status == "verified",
                )
                self.check(
                    "stage_d_A:new_claim_re_reviewed",
                    any(
                        h.kind == "review"
                        and h.review_result.requirement_id == target
                        and h.review_result.status == "passed"
                        for h in recovered
                    ),
                )
            elif letter == "B":
                self.check(
                    "stage_d_B:repeat_rejection_terminal",
                    not data["real_provider_used"]
                    and outcome.metrics.recovery_failed_count == 1
                    and outcome.metrics.recovery_rounds_used == 1
                    and after[target].status == "review_rejected",
                )
            else:
                self.check(
                    "stage_d_C:no_recovery_workers_or_reviews",
                    not data["real_provider_used"]
                    and not outcome.tasks
                    and not recovered
                    and outcome.metrics.recovery_attempted_count
                    == outcome.metrics.recovery_rounds_used
                    == 0,
                )
            # 原通过结果必须保留为同一正文与引用，不能被恢复任务覆写。
            originals = [
                h.review_result
                for h in outcome.merged.history
                if h.kind == "review"
                and h.attempt_index == 0
                and h.review_result.status == "passed"
            ]
            final = {r.requirement_id: r for r in outcome.merged.review_results}
            self.check(
                "stage_d_" + letter + ":initial_approved_immutable",
                all(final[r.requirement_id] == r for r in originals),
            )
            self.summaries["stage_d_" + letter] = {
                "mode": data["mode"],
                "real_provider_used": data["real_provider_used"],
                "controlled_initial_rejection": data["initial_rejection_is_controlled"],
                "recovery_rounds": outcome.metrics.recovery_rounds_used,
                "attempted": outcome.metrics.recovery_attempted_count,
                "passed": outcome.metrics.recovery_passed_count,
                "history_entries": len(outcome.merged.history),
                "recovery_worker_llm_calls": worker_calls,
                "recovery_reviewer_model_calls": review_calls,
                "synthesis_model_calls": outcome.report.metrics.synthesis_model_calls,
                "additional_model_calls": models,
                "verified_requirements": len(source.verified_claims),
                "missing_requirements": len(source.missing_evidence),
            }

    def stage_e(self):
        for suffix, label in [
            ("StageE双论文真实评测", "two_paper"),
            ("StageE三论文真实评测", "three_paper"),
        ]:
            data = self.read(suffix)
            outcome = RecoveryOutcome.model_validate(data["outcome"])
            plan = ResearchPlan.model_validate(data["plan"])
            source, audit = self.rebuilt_source(label, plan, outcome)
            self.check(
                label + ":current_corpus_snapshot",
                data["corpus_snapshot"] == self.snapshot
                and data["configuration"]["corpus_snapshot_id"]
                == self.snapshot["snapshot_id"],
            )
            frozen = next(c for c in self.dataset.cases if c.case_id == data["case_id"])
            self.check(
                label + ":frozen_plan",
                plan == frozen.plan
                and data["planner_mode"] == "replay_original_verified_plan"
                and data["planner_model_calls"] == 0,
            )
            workers = [
                WorkerResult.model_validate(w) for w in data["initial_worker_results"]
            ]
            reviews = [
                ReviewResult.model_validate(r) for r in data["initial_review_results"]
            ]
            llm_calls = (
                sum(w.trace.llm_calls for w in workers)
                + sum(r.trace.model_calls for r in reviews)
                + outcome.report.metrics.synthesis_model_calls
            )
            vlm_calls = sum(w.trace.vlm_tool_attempts for w in workers)
            self.check(
                label + ":real_provider_flag_and_recorded_calls",
                data["real_provider_used"] is True
                and data["quality_result_usable"] is True
                and data["models"]["llm"] != "scripted"
                and llm_calls > 0
                and llm_calls == data["llm_provider_attempts_observed"],
            )
            self.check(label + ":no_vlm_counter_ambiguity_in_this_case", vlm_calls == 0)
            metric = research_metrics(
                outcome,
                initial_workers=workers,
                initial_reviews=reviews,
                wall_time_ms=data["metrics"]["wall_time_ms"],
                corpus_report=self.snapshot,
            )
            self.check(label + ":all_metrics_recomputed", metric == data["metrics"])
            passed = [
                r
                for r in outcome.merged.review_results
                if r.status == "passed" and r.validation_status == "passed"
            ]
            total = len(expand_requirements(plan))
            self.check(
                label + ":coverage_from_reviewer_passes",
                len(passed) == len(source.verified_claims)
                and math.isclose(
                    len(passed) / total, data["metrics"]["verified_coverage"]
                ),
            )
            self.check(
                label + ":all_unresolved_cells_preserved",
                total == len(source.verified_claims) + len(source.missing_evidence)
                and outcome.report.missing_evidence == source.missing_evidence,
            )
            for w in workers:
                tr = w.trace
                self.check(
                    label + ":worker_budget_" + w.paper_id,
                    tr.model_calls
                    <= data["configuration"]["runtime"]["max_model_calls_per_worker"]
                    and tr.tool_calls
                    <= data["configuration"]["runtime"]["max_tool_calls_per_worker"]
                    and tr.protocol_corrections
                    <= data["configuration"]["runtime"]["max_protocol_corrections"],
                )
                self.check(
                    label + ":decision_trace_" + w.paper_id,
                    tr.retry_count == 0
                    and len(tr.actions) == tr.llm_calls
                    and all(
                        a.get("execution_status") == "not_executed"
                        for a in tr.actions
                        if a.get("issue_code") == "multiple_actions"
                    ),
                )
            self.check(
                label + ":model_tool_totals",
                data["metrics"]["total_model_calls"] == llm_calls
                and data["metrics"]["total_tool_calls"]
                == sum(w.trace.tool_calls for w in workers),
            )
            if label == "three_paper":
                # 仅核对用户给出的历史参考结果，不修改任何产物或验收阈值。
                self.check(
                    label + ":historical_reference_values",
                    total == 15
                    and len(passed) == 14
                    and math.isclose(len(passed) / total, 14 / 15)
                    and audit["cross_paper_comparison_count"] == 4
                    and outcome.report.metrics.citation_validation_failures == 0
                    and outcome.report.status == "completed",
                )
            self.summaries[label] = {
                "case_id": data["case_id"],
                "status": data["status"],
                "total_requirements": total,
                "verified_requirements": len(passed),
                "verified_coverage": len(passed) / total,
                **audit,
                "citation_validation_failures": outcome.report.metrics.citation_validation_failures,
                "model_calls": llm_calls,
                "tool_calls": data["metrics"]["total_tool_calls"],
                "vlm_tool_attempts": vlm_calls,
                "missing": [m.model_dump(mode="json") for m in source.missing_evidence],
                "protocol_corrections": sum(
                    w.trace.protocol_corrections for w in workers
                ),
                "provider_evidence_level": "Saved local execution traces and aggregate adapter counters; no independent Provider billing receipt.",
                "semantic_accuracy_measured": False,
            }

    def retrieval(self):
        data = self.read("StageE真实检索回归")
        self.check(
            "retrieval:real_embedding_only",
            data["status"] == "retrieval_executed"
            and data["models_initialized"] == 1
            and data["real_provider_calls"] == 0,
        )
        self.check(
            "retrieval:dataset_snapshot_and_config",
            data["corpus_snapshot"] == self.snapshot
            and data["dataset_sha256"]
            == canonical_hash(self.dataset.model_dump(mode="json")),
        )
        corpus = {e.evidence_id: e for e in self.corpus.text_evidence}
        cases = {
            c.case_id: c for c in self.dataset.cases if c.case_id.startswith("core-")
        }
        values = {}
        for name in ["before_stage_d_corpus", "after_stage_e_corpus"]:
            report = data["retrieval_metrics"][name]
            rows = report["cases"]
            self.check(
                "retrieval:" + name + ":sample_count",
                report["sample_count"] == len(rows) == 15
                and {r["case_id"] for r in rows} == set(cases),
            )
            for row in rows:
                case = cases[row["case_id"]]
                original = expand_requirements(case.plan)[0]
                relevant, found = (
                    set(row["relevant_evidence_ids"]),
                    row["retrieved_evidence_ids"],
                )
                self.check(
                    "retrieval:" + name + ":labels_" + row["case_id"],
                    original.requirement_id == row["requirement_id"]
                    and original.paper_id == row["paper_id"]
                    and relevant == set(case.source_labels[original.requirement_id]),
                )
                self.check(
                    "retrieval:" + name + ":scope_" + row["case_id"],
                    len(found) == len(set(found))
                    and len(found) <= 10
                    and all(
                        i in corpus and corpus[i].paper_id == row["paper_id"]
                        for i in found
                    ),
                )
                self.check(
                    "retrieval:" + name + ":recall_" + row["case_id"],
                    math.isclose(
                        row["recall_at_5"],
                        len(set(found[:5]) & relevant) / len(relevant),
                    )
                    and math.isclose(
                        row["recall_at_10"], len(set(found) & relevant) / len(relevant)
                    ),
                )
            self.check(
                "retrieval:" + name + ":macro_recalculated",
                all(
                    math.isclose(
                        report[f"recall_at_{k}"],
                        sum(r[f"recall_at_{k}"] for r in rows) / len(rows),
                    )
                    for k in [5, 10]
                ),
            )
            values[name] = {
                k: report[k] for k in ["sample_count", "recall_at_5", "recall_at_10"]
            }
        self.summaries["retrieval"] = values
        if data.get("reason") == "user_confirmation_required_before_real_evaluation":
            self.warnings.append(
                {
                    "severity": "non_blocking",
                    "code": "stale_preflight_reason",
                    "description": "Historical retrieval artifact retains its preflight reason despite retrieval_executed status and complete actual rows. Original artifact is preserved; execution classification follows validated status/counters/results.",
                }
            )

    def historical_chain(self):
        manifest = json.loads((self.directory / "artifact_manifest.json").read_text())
        for item in manifest["artifacts"]:
            self.check(
                "artifact_sha256:" + item["path"],
                not item.get("missing")
                and digest(self.directory / item["path"]) == item["sha256"],
            )
        worker_file = next(
            (self.directory / "evidence").glob("*StageA相同FlamingoSmoke.json")
        )
        review_file = next(
            (self.directory / "evidence").glob("*StageB真实EvidenceReviewSmoke.json")
        )
        review, synthesis = (
            self.read("StageB真实EvidenceReviewSmoke"),
            self.read("StageC真实研究报告"),
        )
        self.check(
            "stage_ab:worker_review_source_hash",
            review["source_worker_report_sha256"] == digest(worker_file),
        )
        self.check(
            "stage_c:source_hashes",
            synthesis["worker_report_sha256"] == digest(worker_file)
            and synthesis["review_report_sha256"] == digest(review_file),
        )
        planner = self.read("ResearchPlanner三条真实Smoke", "2026-10-07")["cases"][0]
        original = ResearchPlan.model_validate(
            {k: planner[k] for k in ["question", "paper_ids", "dimensions"]}
        )
        self.check(
            "planner:original_real_plan_retained",
            original == self.dataset.original_plan
            and len(expand_requirements(original)) == 15
            and planner["status"] == "success"
            and planner["trace"]["model_calls"] == 1,
        )
        workers = [
            WorkerResult.model_validate(w)
            for w in self.read("StageA相同FlamingoSmoke")["worker_results"]
        ]
        reviews = [ReviewResult.model_validate(r) for r in review["results"]]
        source = SynthesisInputBuilder(self.resolver).build(original, workers, reviews)
        old_report = ResearchReport.model_validate(synthesis["research_report"])
        audit_report(source, old_report)
        self.check(
            "stage_c:original_report_revalidated",
            len(source.verified_claims) == 5 and len(source.missing_evidence) == 10,
        )
        degraded = self.read("StageC受控失败降级报告")
        fallback = ResearchReport.model_validate(degraded["research_report"])
        audit_report(source, fallback)
        self.check(
            "stage_c:deterministic_fallback_keeps_reviewed_facts",
            degraded["real_provider_calls"] == 0
            and fallback.status == "generation_degraded"
            and fallback.generation_degraded
            and fallback.verified_claims == old_report.verified_claims
            and fallback.missing_evidence == old_report.missing_evidence,
        )
        for label in ["StageE双论文受控Smoke", "StageE三论文受控Smoke"]:
            fake = self.read(label)
            self.check(
                label + ":clearly_scripted",
                fake["status"] == "scripted_wiring_only"
                and fake["real_provider_used"] is False
                and fake["real_provider_calls"] == 0
                and fake["quality_result_usable"] is False
                and fake["metrics"]["coverage_delta_vs_stage_d"] is None,
            )

    def run(self):
        self.historical_chain()
        self.stage_d()
        self.stage_e()
        self.retrieval()
        return {
            "conclusion": "PASS"
            if all(c["status"] == "PASS" for c in self.checks)
            else "FAIL",
            "checks": self.checks,
            "summaries": self.summaries,
            "warnings": self.warnings,
            "new_model_calls": 0,
            "existing_results_modified": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--directory", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args()
    result = EvidenceAudit(args.root, args.directory).run()
    (args.directory / "evidence_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "conclusion": result["conclusion"],
                "checks": len(result["checks"]),
                "failures": [c for c in result["checks"] if c["status"] != "PASS"],
                "summaries": result["summaries"],
                "warnings": result["warnings"],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(int(result["conclusion"] != "PASS"))


if __name__ == "__main__":
    main()
