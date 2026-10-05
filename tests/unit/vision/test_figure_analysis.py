import json
from types import SimpleNamespace

import pytest

from app.evidence.models import FigureEvidence
from app.vision.analysis import FigureAnalyzer
from app.vision.client import VLMResponse
from app.vision.provider import OpenAICompatibleVisionClient


class FakeVisionLanguageClient:
    def __init__(self, evidence_id="ev-figure", finish_reason="stop"):
        self.evidence_id, self.finish_reason = evidence_id, finish_reason

    def analyze(self, image_path, prompt):
        self.received = image_path, prompt
        return VLMResponse(
            text=json.dumps(
                {
                    "observations": ["A captioner and filter are connected by arrows."],
                    "interpretation": "This is a pipeline diagram, not proof of superior performance.",
                    "evidence_used": [self.evidence_id],
                    "limitations": ["No benchmark values shown."],
                }
            ),
            usage={"completion_tokens": 40},
            finish_reason=self.finish_reason,
        )


@pytest.fixture
def figure(tmp_path):
    asset = tmp_path / "figure.png"
    asset.write_bytes(b"fake image; provider is not called")
    return FigureEvidence(
        evidence_id="ev-figure",
        paper_id="blip-v2",
        page=3,
        text="A captioner creates captions and a filter removes noisy pairs.",
        figure_id="Figure 2",
        caption="CapFilt pipeline",
        asset_path=str(asset),
    )


def test_prompt_contains_visual_source_and_context_and_structured_result(figure):
    client = FakeVisionLanguageClient()
    result = FigureAnalyzer(client).analyze_figure(
        figure, "Why does it prove better performance?"
    )
    path, prompt = client.received
    assert path == figure.asset_path
    for text in [
        figure.caption,
        figure.text,
        "PDF page: 3",
        "observations",
        "does not prove",
    ]:
        assert text in prompt
    assert result.observations
    assert "not proof" in result.interpretation
    assert result.evidence_used == [figure.evidence_id]
    assert result.usage == {"completion_tokens": 40}
    assert result.finish_reason == "stop"


@pytest.mark.parametrize("question", ["", "  "])
def test_empty_question_rejected(figure, question):
    with pytest.raises(ValueError):
        FigureAnalyzer(FakeVisionLanguageClient()).analyze_figure(figure, question)


def test_missing_asset_rejected(figure):
    figure = figure.model_copy(update={"asset_path": "/missing/figure.png"})
    with pytest.raises(FileNotFoundError):
        FigureAnalyzer(FakeVisionLanguageClient()).analyze_figure(figure, "Explain")


def test_unknown_evidence_and_truncated_output_rejected(figure):
    with pytest.raises(ValueError, match="unknown evidence"):
        FigureAnalyzer(FakeVisionLanguageClient("invented")).analyze_figure(
            figure, "Explain"
        )
    with pytest.raises(ValueError, match="Incomplete"):
        FigureAnalyzer(FakeVisionLanguageClient(finish_reason="length")).analyze_figure(
            figure, "Explain"
        )


def test_provider_sends_actual_image_bytes_and_preserves_usage(figure):
    class FakeCompletions:
        def create(self, **kwargs):
            self.received = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"result":"ok"}'),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 52}),
            )

    completions = FakeCompletions()
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client = OpenAICompatibleVisionClient(
        api_key="fake", model="fake", base_url="https://unused", sdk_client=sdk
    )
    response = client.analyze(figure.asset_path, "Explain")
    parts = completions.received["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "Explain"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert response.usage == {"total_tokens": 52}
