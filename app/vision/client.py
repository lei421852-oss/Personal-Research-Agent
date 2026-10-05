"""Provider-independent boundary for a single visual-evidence request."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class VLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None


class VisionLanguageClient(Protocol):
    def analyze(self, image_path: str, prompt: str) -> VLMResponse: ...
