"""冻结默认 Worker 的协议适配；只改模型请求副本，不改响应或执行约束。"""

import json
from copy import deepcopy

from app.tools.redaction import public_value

PROTOCOL_INSTRUCTION = """One action means exactly one function/tool call in this response.
Never return multiple tool calls. Wait for the observation before the next call.
resolve_requirements may batch resolutions INSIDE one call; it does not authorize
multiple function calls. Tool argument names are exact, not interchangeable.
For search_text use the paper_ids ARRAY; paper_id is only for read_page/find_figure.
Follow protocol_feedback and the selected tool's schema. Examples show wire format
only: replace placeholder query text with your information need, not paper facts.
"""


def protocol_request(messages, tools, previous_action=None):
    """按照当前 Worker scope 和实际 Schema 生成协议视图；不选择下一动作。"""
    messages, tools = deepcopy(messages), deepcopy(tools)
    context = json.loads(messages[-1]["content"])
    paper_id = context["paper_id"]
    examples, contracts = {}, {}
    for tool in tools:
        schema = tool["input_schema"]
        fields = schema.get("properties", {})
        if tool["name"] == "search_text":
            # 共用工具允许全库查询；Worker Controller 实际仅接受本论文单元素 scope。
            # 模型所见副本准确表达已有约束，原 input_model 和 Controller 均不变。
            scope = fields["paper_ids"]
            array = next(
                option
                for option in scope.get("anyOf", [scope])
                if option.get("type") == "array"
            )
            local = deepcopy(array)
            local.update(
                minItems=1,
                maxItems=1,
                description="Required array with exactly this worker's assigned paper ID; never use paper_id or null.",
            )
            local["items"]["enum"] = [paper_id]
            fields["paper_ids"] = local
            schema["required"] = list(
                dict.fromkeys([*schema.get("required", []), "paper_ids"])
            )
            tool["description"] = (
                "Search prepared text inside this assigned paper only. Use paper_ids "
                "as a one-element array, never the singular paper_id field. "
                "Submit only this one function call, then wait for its observation."
            )
            examples[tool["name"]] = {
                "name": tool["name"],
                "arguments": {
                    "query": "<replace with your current information need>",
                    "paper_ids": [paper_id],
                    "top_k": fields["top_k"].get("default", 3),
                },
            }
        contracts[tool["name"]] = {
            "allowed_fields": list(fields),
            "required_fields": schema.get("required", []),
            "additional_fields_allowed": schema.get("additionalProperties", True),
        }
    feedback = []
    # 成功调用后的历史错误仍留在原上下文，但不能继续当作尚未解决的纠正指令。
    for observation in context.get("recent_observations", [])[-1:]:
        payload = observation.get("payload", {})
        code = (observation.get("issue") or {}).get("code") or payload.get("issue_code")
        if code not in {"multiple_actions", "invalid_arguments"}:
            continue
        name = observation["tool_name"]
        # 冻结 Worker 的通用参数失败 Observation 使用 worker_controller 名称。
        # 只按同一轮的公开 Trace 绑定实际工具，不凭字段名猜测下一工具。
        if (
            name == "worker_controller"
            and previous_action
            and previous_action.get("issue_code") == code
        ):
            name = previous_action["tool_name"]
        item = {
            "issue_code": code,
            "tool_name": name,
            "expected_action_count": 1,
            "instruction": "Resubmit exactly one legal action. No rejected tool was executed. Correct the named fields; do not retry the same invalid arguments.",
        }
        if code == "multiple_actions":
            item["actual_action_count"] = payload["actual_action_count"]
            item["valid_example"] = examples.get("search_text")
        else:
            item["field_errors"] = (payload.get("failure") or {}).get(
                "validation_errors", []
            )
            item["argument_contract"] = contracts.get(name)
            item["valid_example"] = examples.get(name)
        feedback.append(item)
    context["worker_protocol"] = {
        "actions_per_response": 1,
        "argument_contracts": contracts,
        "valid_examples": examples,
        "batch_resolution_semantics": "Multiple resolutions may appear inside ONE resolve_requirements call, never as multiple tool calls.",
    }
    context["protocol_feedback"] = feedback
    messages[0]["content"] += "\n" + PROTOCOL_INSTRUCTION
    messages[-1]["content"] = json.dumps(public_value(context), ensure_ascii=False)
    return messages, tools, feedback


class WorkerProtocolAdapter:
    """透明组合原 AsyncLLMAdapter，完整转发预算、统计、取消和返回动作。"""

    def __init__(self, adapter, *, action_source=None):
        self.adapter = adapter
        self.action_source = action_source
        self.resource_gate = adapter.resource_gate
        self.feedback_events = []
        self.decision_requests = 0

    def snapshot(self):
        return self.adapter.snapshot()

    async def drain(self):
        await self.adapter.drain()

    async def complete(self, *, messages, tools, attempt_budget=None):
        previous = self.action_source() if self.action_source else None
        messages, tools, feedback = protocol_request(messages, tools, previous)
        self.decision_requests += 1
        if feedback:
            self.feedback_events.append(
                {"decision_request": self.decision_requests, "feedback": feedback}
            )
        # 严禁截断 multiple actions、改名字段或代替模型重试；原 Controller 继续校验。
        return await self.adapter.complete(
            messages=messages, tools=tools, attempt_budget=attempt_budget
        )
