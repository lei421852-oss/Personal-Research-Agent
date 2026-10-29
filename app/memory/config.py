"""显式启用；禁用时不读取身份配置或导入SQLAlchemy。"""

import os

from pydantic import BaseModel, ConfigDict, Field

from app.memory.errors import MemoryFailure


class MemorySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    enabled: bool = False
    project_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
    )

    @classmethod
    def from_environment(cls, environment=None):
        source = os.environ if environment is None else environment
        flag = source.get("AGENT_MEMORY_ENABLED", "false")
        if flag not in {"true", "false"}:
            raise MemoryFailure("memory_configuration_invalid")
        if flag == "false":
            return cls()
        try:
            result = cls(enabled=True, project_id=source.get("AGENT_MEMORY_PROJECT_ID"))
            if result.project_id is None:
                raise ValueError
            return result
        except (ValueError, TypeError):
            raise MemoryFailure("memory_configuration_invalid") from None
