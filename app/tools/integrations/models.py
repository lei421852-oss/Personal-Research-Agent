"""外部工具定义与管理员策略；服务返回内容无权改变这些策略。"""

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,19}\Z")


class ExternalServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider: str
    enabled: bool = False
    allowed_tools: tuple[str, ...] = ()
    permission_level: Literal["read_only"] = "read_only"
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=30, allow_inf_nan=False)
    max_input_bytes: int = Field(default=65536, ge=64, le=1048576)
    max_output_bytes: int = Field(default=65536, ge=64, le=1048576)
    max_discovered_tools: int = Field(default=16, ge=1, le=32)

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("invalid_external_provider")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def valid_allowlist(cls, value):
        if len(value) > 32 or len(value) != len(set(value)):
            raise ValueError("invalid_external_allowlist")
        if any(not IDENTIFIER.fullmatch(name) for name in value):
            raise ValueError("invalid_external_tool_name")
        return value

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str], *, provider: str, allowed_tools=()
    ):
        """沿用独立环境字典，不改全局环境，不接受命令、URL或认证值。"""
        try:
            flag = environment.get("AGENT_EXTERNAL_TOOLS_ENABLED", "false")
            if flag not in {"true", "false"}:
                raise ValueError
            return cls(
                provider=provider,
                enabled=flag == "true",
                allowed_tools=tuple(allowed_tools),
                timeout_seconds=float(
                    environment.get("AGENT_EXTERNAL_TOOL_TIMEOUT_SECONDS", "5")
                ),
                max_output_bytes=int(
                    environment.get("AGENT_EXTERNAL_TOOL_MAX_OUTPUT_BYTES", "65536")
                ),
            )
        except (ValueError, TypeError):
            # 不回显原始配置值，避免错误中带入连接信息或密码。
            raise ValueError("invalid_external_tool_configuration") from None


class ExternalToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, JsonValue]
    # 缺少只读声明时默认拒绝，不能凭工具名字猜测是否有副作用。
    permission: Literal["read_only", "side_effecting"] = "side_effecting"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("invalid_external_tool_name")
        return value


def namespaced_name(provider: str, name: str) -> str:
    if not IDENTIFIER.fullmatch(provider) or not IDENTIFIER.fullmatch(name):
        raise ValueError("invalid_external_tool_name")
    # 兼容现有提供方函数名称限制，避免点号命名被提供方拒绝。
    return f"external__{provider}__{name}"
