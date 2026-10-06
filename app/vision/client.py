"""单次视觉证据请求的统一接口。"""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class VLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None


class VisionLanguageClient(Protocol):
    def analyze(self, image_path: str, prompt: str) -> VLMResponse: ...
