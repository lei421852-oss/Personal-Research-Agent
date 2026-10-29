"""将适配层错误收口到已有Observation；不新增公共状态或重试协议。"""

from app.tools.observations import ObservationIssue, ToolObservation

ERRORS = {
    "name_conflict": ("internal_failure", "外部工具名称冲突，未覆盖现有工具。"),
    "unknown_tool": ("unknown_tool", "外部工具不存在。"),
    "invalid_arguments": ("invalid_arguments", "外部工具参数不符合契约。"),
    "input_limit": ("invalid_arguments", "外部工具输入超过限制。"),
    "permission_denied": ("invalid_arguments", "外部工具未启用或不在只读白名单内。"),
    "unsupported_schema": ("invalid_arguments", "外部工具参数模式包含不支持的特性。"),
    "unavailable": ("dependency_failure", "外部工具服务不可用。"),
    "timeout": ("dependency_failure", "外部工具调用预算已耗尽。"),
    "cancelled": ("internal_failure", "外部工具调用已取消，未自动重试。"),
    "invalid_output": ("internal_failure", "外部工具返回了无效协议数据。"),
    "output_limit": ("internal_failure", "外部工具输出超过限制。"),
    "cleanup_failed": ("internal_failure", "无法确认外部工具资源已经清理。"),
    "closed": ("internal_failure", "外部工具会话已关闭。"),
    "internal_failure": ("internal_failure", "外部工具执行发生内部错误。"),
}


class ExternalToolError(Exception):
    def __init__(self, code, *, execution=None):
        self.code = code if code in ERRORS else "internal_failure"
        self.execution = execution or {}
        super().__init__(self.code)


def error_observation(name, provider, error):
    code, summary = ERRORS[error.code]
    return ToolObservation(
        status="error",
        tool_name=name,
        summary=summary,
        payload={
            "provider": provider,
            "external_error_code": error.code,
            "execution": error.execution,
            "trust": "untrusted_external_data",
        },
        issue=ObservationIssue(code=code, retryable=code == "dependency_failure"),
    )
