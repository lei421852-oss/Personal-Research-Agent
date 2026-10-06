"""显式启用支持视觉输入的 Chat Completions 提供方适配器。

不默认选择模型；兼容性由用户的真实冒烟测试验证。
"""

import base64
import mimetypes
from pathlib import Path
from typing import Any

from app.tools.errors import DependencyFailure
from app.vision.client import VLMResponse


class OpenAICompatibleVisionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 60,
        sdk_client: Any = None,
    ):
        if not api_key or not model.strip() or timeout <= 0:
            raise ValueError("api_key, model and a positive timeout are required")
        if sdk_client is None:
            from openai import OpenAI

            sdk_client = OpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0
            )
        self.client = sdk_client
        self.model = model

    def analyze(self, image_path: str, prompt: str) -> VLMResponse:
        path = Path(image_path)
        media_type = mimetypes.guess_type(path.name)[0]
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Expected a PNG, JPEG or WebP image")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{encoded}"
                                },
                            },
                        ],
                    }
                ],
            )
        except Exception as exc:
            raise DependencyFailure("Vision provider request failed") from exc
        if not response.choices:
            raise ValueError("Vision provider returned no choices")
        choice = response.choices[0]
        if not choice.message.content:
            raise ValueError("Vision provider returned no text")
        usage = response.usage.model_dump() if response.usage is not None else None
        return VLMResponse(
            text=choice.message.content, usage=usage, finish_reason=choice.finish_reason
        )

    def close(self) -> None:
        self.client.close()
