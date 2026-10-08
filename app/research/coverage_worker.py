"""显式启用的 F3.1 Worker；只扩展覆盖协议，复用冻结循环与资源控制。"""

from copy import deepcopy

from app.research.coverage_models import (
    ASPECTS_BY_DIMENSION,
    DEFAULT_ASPECTS,
    BatchResolution,
    CoverageAspect,
    CoverageResolution,
)
from app.research.models import WorkerResult
from app.research.worker import PaperResearchWorker
from app.tools.redaction import public_value

STAGE_ASPECTS = {
    "frozen_trainable_scope": ("frozen_modules", "updated_modules"),
    "training_objective": ("losses", "optimization_targets"),
}

COVERAGE_POLICY = """F3.1 paper-local coverage protocol:
The updated resolve schemas supersede the legacy free-claim arguments. Supply coverage
and stages only; the authoritative concise claim is assembled from their facts.
Your assignment is one paper and one technical dimension per requirement. The original
request may mention several papers; it is context for the user's intent, not permission
to state peer-paper mechanisms or independently perform the global comparison.
Use coverage_assignments and only observed evidence. Do not add performance numbers
outside the requested dimension. Report each expected aspect as covered, missing, or
not_applicable. An observed supporting fact does not make the whole requirement complete.
covered needs the actual requested property: 'frozen LLM' alone does not identify its
family; a connector name alone does not describe its visual transformation and LM input.
For stage-sensitive dimensions, identify the documented stages first and cover each
stage's properties separately. Stage 2 facts do not establish Stage 1 facts. Do not
claim a complete stage inventory merely because one stage was retrieved.
not_applicable requires positive observed evidence and a supporting source quote;
not retrieved or not stated means missing, never not_applicable.
Facts must declare this paper's subject_paper_id and the current dimension_id.
Use exclusive only for a positively documented exclusive scope, with the exact supporting
quote. Do not infer whole-paper absence from local retrieval. limited_absence is limited
to supplied evidence, never the entire paper. These are semantic obligations, not a word ban.
No free-standing claim is accepted: the program assembles it from the submitted aspect facts.
When coverage is missing, investigate the specific gap within remaining budgets. If the
prepared evidence cannot fill the gap, resolve insufficient_evidence with the coverage
record; partial facts stay diagnostic and are not verified report content. Budget or
dependency failure is not evidence insufficiency. Do not endlessly confirm existing facts.
Make exactly one action, wait for feedback, and never disclose hidden reasoning.
"""


class CoveragePolicyAdapter:
    """正常组合异步适配器；不另发请求、不改变重试或预算计数。"""

    def __init__(self, adapter):
        self.adapter = adapter
        self.resource_gate = adapter.resource_gate

    def snapshot(self):
        return self.adapter.snapshot()

    async def drain(self):
        await self.adapter.drain()

    async def complete(self, *, messages, tools, attempt_budget=None):
        messages = deepcopy(messages)
        messages[0]["content"] += "\n" + COVERAGE_POLICY
        return await self.adapter.complete(
            messages=messages, tools=tools, attempt_budget=attempt_budget
        )


def expected_aspects(dimension_id):
    return ASPECTS_BY_DIMENSION.get(dimension_id, DEFAULT_ASPECTS)


class CoverageAwarePaperWorker(PaperResearchWorker):
    """新入口的覆盖门槛；旧 WorkerResult、状态及引用契约保持不变。"""

    def __init__(self, llm_adapter, *args, **kwargs):
        super().__init__(CoveragePolicyAdapter(llm_adapter), *args, **kwargs)
        self._reset_coverage()

    def _reset_coverage(self):
        self._assessments = {}
        self._submissions = []
        self._gap_baseline = None
        self.last_coverage_report = None

    async def run(self, task):
        # 先拒绝并发重入，不能让第二次调用清掉仍在执行的 sidecar。
        if self._running:
            raise RuntimeError("One coverage worker cannot run concurrent tasks")
        self._reset_coverage()
        return await super().run(task)

    def _schemas(self):
        schemas = super()._schemas()
        for schema in schemas:
            if schema["name"] == "resolve_requirement":
                schema["input_schema"] = CoverageResolution.model_json_schema()
                schema["description"] = (
                    "Submit one paper-local coverage assessment; supported requires complete aspects and stage coverage."
                )
            elif schema["name"] == "resolve_requirements":
                schema["input_schema"] = BatchResolution.model_json_schema()
                schema["description"] = (
                    "Atomically submit coverage for several pending requirements."
                )
        return schemas

    def _context(self):
        context = super()._context()
        assignments = []
        for requirement in self.state.requirements:
            dimension = requirement.dimension_id
            assignments.append(
                {
                    "requirement_id": requirement.requirement_id,
                    "paper_id": self.state.paper_id,
                    "dimension_id": dimension,
                    "local_objective": "Report only this assigned paper's documented "
                    + dimension.replace("_", " ")
                    + " facts. Global comparisons are assembled above this worker.",
                    "expected_aspects": list(expected_aspects(dimension)),
                    "expected_aspects_per_stage": list(
                        STAGE_ASPECTS.get(dimension, ())
                    ),
                    "current_assessment": self._assessments.get(
                        requirement.requirement_id
                    ),
                }
            )
        context["coverage_assignments"] = assignments
        context["coverage_semantics"] = (
            "Policy judges support and applicability; Controller checks explicit identity, observed citations and structural coverage. Original requirement identities/questions remain unchanged."
        )
        return context

    def _validate_aspects(self, aspects, expected, requirement):
        if {a.aspect_id for a in aspects} != set(expected):
            raise ValueError("Coverage must list every expected aspect exactly once")
        for aspect in aspects:
            fact = aspect.fact
            if fact is None:
                continue
            if (
                fact.subject_paper_id != self.state.paper_id
                or fact.dimension_id != requirement.dimension_id
            ):
                raise ValueError(
                    "Coverage fact subject or dimension is outside this assignment"
                )
            for identity in fact.evidence_ids:
                if identity not in self.state.collected_evidence:
                    raise ValueError("Coverage cites unobserved evidence")
                if self.resolver.resolve(identity).paper_id != self.state.paper_id:
                    raise ValueError("Coverage evidence belongs to another paper")
            needs_quote = (
                aspect.status == "not_applicable"
                or fact.assertion_kind != "descriptive"
            )
            if needs_quote and (
                not fact.support_quote
                or not any(
                    fact.support_quote in self.resolver.resolve(i).text
                    for i in fact.evidence_ids
                )
            ):
                raise ValueError(
                    "Applicability/exclusive scope needs a quote from cited observed evidence"
                )

    def _validate_resolution(self, resolution):
        requirement = next(
            (
                r
                for r in self.state.requirements
                if r.requirement_id == resolution.requirement_id
            ),
            None,
        )
        if requirement is None or requirement.status != "pending":
            raise ValueError("Resolve only a known pending requirement")
        self._validate_aspects(
            resolution.coverage, expected_aspects(requirement.dimension_id), requirement
        )
        stage_keys = STAGE_ASPECTS.get(requirement.dimension_id)
        if resolution.stages and not stage_keys:
            raise ValueError("Stage details are not an aspect of this dimension")
        for stage in resolution.stages:
            self._validate_aspects(stage.aspects, stage_keys, requirement)
        gaps = [a.aspect_id for a in resolution.coverage if a.status == "missing"]
        for stage in resolution.stages:
            gaps.extend(
                stage.stage_id + ":" + a.aspect_id
                for a in stage.aspects
                if a.status == "missing"
            )
        stage_inventory = next(
            (a for a in resolution.coverage if a.aspect_id == "training_stages"), None
        )
        if stage_keys and stage_inventory.status == "covered" and not resolution.stages:
            gaps.append("stage_details")
        if (
            stage_keys
            and stage_inventory.status == "not_applicable"
            and resolution.stages
        ):
            raise ValueError(
                "A nonapplicable stage inventory cannot also declare stages"
            )
        facts = [a.fact for a in resolution.coverage if a.fact]
        facts += [a.fact for s in resolution.stages for a in s.aspects if a.fact]
        # N/A 依据也必须进入正式引用，不能绕过原 Reviewer 的引文范围。
        evidence_ids = list(dict.fromkeys(i for f in facts for i in f.evidence_ids))
        claim = "\n".join(f.text for f in facts)
        return gaps, claim, evidence_ids

    def _resolve(self, name, arguments):
        resolutions = (
            [CoverageResolution.model_validate(arguments)]
            if name == "resolve_requirement"
            else BatchResolution.model_validate(arguments).resolutions
        )
        if len({r.requirement_id for r in resolutions}) != len(resolutions):
            raise ValueError("Duplicate coverage requirement resolution")
        # 全批先校验，失败时不得更新正式状态或部分接受 sidecar。
        checked = [self._validate_resolution(r) for r in resolutions]
        rejected = any(
            r.status == "supported" and gaps
            for r, (gaps, _, _) in zip(resolutions, checked, strict=True)
        )
        snapshots = [
            r.model_dump(mode="json") | {"complete": not gaps, "missing_aspects": gaps}
            for r, (gaps, _, _) in zip(resolutions, checked, strict=True)
        ]
        if rejected:
            for resolution, snapshot in zip(resolutions, snapshots, strict=True):
                self._assessments[resolution.requirement_id] = snapshot
            if self._gap_baseline is None:
                self._gap_baseline = self._counts()
            self._submissions.append(
                {
                    "step": len(self.state.actions) + 1,
                    "accepted": False,
                    "coverage": snapshots,
                }
            )
            return self._feedback(
                name,
                "invalid_arguments",
                "Coverage is incomplete. Investigate the specific missing aspects or explicitly finish with insufficient evidence; no requirement was resolved.",
                {
                    "coverage_issue": "requirement_coverage_incomplete",
                    "missing_aspects": {
                        r.requirement_id: gaps
                        for r, (gaps, _, _) in zip(resolutions, checked, strict=True)
                        if gaps
                    },
                    "coverage": snapshots,
                },
            )
        legacy = []
        for resolution, (gaps, claim, identities) in zip(
            resolutions, checked, strict=True
        ):
            if resolution.status == "insufficient_evidence" and not gaps:
                raise ValueError(
                    "Insufficient evidence must retain an explicit coverage gap"
                )
            supported = resolution.status == "supported"
            legacy.append(
                {
                    "requirement_id": resolution.requirement_id,
                    "status": resolution.status,
                    "claim": claim if supported else None,
                    "evidence_ids": identities if supported else [],
                }
            )
        observation = super()._resolve("resolve_requirements", {"resolutions": legacy})
        for resolution, snapshot in zip(resolutions, snapshots, strict=True):
            self._assessments[resolution.requirement_id] = snapshot
        self._submissions.append(
            {
                "step": len(self.state.actions) + 1,
                "accepted": True,
                "coverage": snapshots,
            }
        )
        return observation.model_copy(update={"tool_name": name})

    def _result(self, task, started, **kwargs):
        result = super()._result(task, started, **kwargs)
        requirements = []
        for requirement in result.requirements:
            assessment = self._assessments.get(requirement.requirement_id)
            if assessment is None:
                assessment = {
                    "requirement_id": requirement.requirement_id,
                    "coverage": [
                        CoverageAspect(aspect_id=i, status="missing").model_dump(
                            mode="json"
                        )
                        for i in expected_aspects(requirement.dimension_id)
                    ],
                    "stages": [],
                    "complete": False,
                    "missing_aspects": list(expected_aspects(requirement.dimension_id)),
                }
            requirements.append(
                deepcopy(assessment)
                | {
                    "dimension_id": requirement.dimension_id,
                    "formal_status": requirement.status.value,
                    "retained_gaps": not assessment["complete"],
                }
            )
        counts = self._counts()
        baseline = self._gap_baseline or counts
        self.last_coverage_report = public_value(
            {
                "version": "F3.1",
                "paper_id": result.paper_id,
                "requirements": requirements,
                "metrics": {
                    "coverage_rejections": sum(
                        not s["accepted"] for s in self._submissions
                    ),
                    "additional_model_calls": counts["model_calls"]
                    - baseline["model_calls"],
                    "additional_tool_calls": counts["tool_calls"]
                    - baseline["tool_calls"],
                    "extra_lookup_observed": counts["tool_calls"]
                    > baseline["tool_calls"],
                },
                "semantics": "Coverage facts are Policy assertions with observed citations, not independently reviewed claims. Additional calls are observed after first coverage rejection, not a causal necessity metric.",
            }
        )
        actions = deepcopy(result.trace.actions)
        submissions = {s["step"]: s for s in self._submissions}
        for action in actions:
            if action["step"] in submissions:
                action["coverage_submission"] = public_value(
                    submissions[action["step"]]
                )
        if actions:
            actions[-1]["coverage_final"] = self.last_coverage_report
        result = WorkerResult.model_validate(
            result.model_dump(mode="json")
            | {"trace": result.trace.model_dump(mode="json") | {"actions": actions}}
        )
        self.last_result = result
        return result
