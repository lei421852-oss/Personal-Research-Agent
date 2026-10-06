"""使用真实 HTTP 服务与明确标记的模拟模型检查浏览器展示。"""

import argparse
import json
import os
import socket
from datetime import datetime
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from zoneinfo import ZoneInfo

import uvicorn
from playwright.sync_api import expect, sync_playwright

from app.api.runtime import AgentAPIRuntime
from app.evidence.corpus import load_paper_corpus
from app.llm.fake import ScriptedLLMClient
from app.main import create_app
from scripts.demo_day5 import (
    ControlledRetrieval,
    ControlledVLM,
    action,
    last_observation,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("/mnt/d/Codex-Generated-Artifacts/2026-10-05-多模态论文研究智能体")


def make_test_runtime():
    corpus = load_paper_corpus(ROOT / "data/papers")

    def clients(stack):
        question = [""]

        def first(messages, tools):
            question[0] = messages[1]["content"]
            if "Figure 3" in question[0]:
                return action(
                    "find", "find_figure", paper_id="blip", figure_id="Figure 3"
                )
            if "SHA-256" in question[0]:
                return action(
                    "finish",
                    "finish",
                    answer="Prepared corpus does not contain enough evidence.",
                    evidence_ids=[],
                    limitations=[
                        "No private dataset manifest or duplicate count was supplied."
                    ],
                )
            return action(
                "search",
                "search_text",
                query="CLIP objective",
                paper_ids=["clip"],
                top_k=1,
            )

        def second(messages, tools):
            payload = last_observation(messages)["payload"]
            if "Figure 3" in question[0]:
                return action(
                    "analyze",
                    "analyze_figure",
                    question=question[0],
                    figure_evidence_id=payload["figure"]["evidence_id"],
                    context_evidence_ids=[],
                )
            answer = "CLIP aligns images and text with a contrastive objective. [Fake model: transport demonstration.]"
            if question[0] == "HTML display check":
                answer = '<script>window.__injected=true</script><img src=x onerror="window.__injected=true">'
            return action(
                "finish",
                "finish",
                answer=answer,
                evidence_ids=[payload["evidence"][0]["evidence_id"]],
                limitations=[],
            )

        def third(messages, tools):
            analysis = last_observation(messages)["payload"]["analysis"]
            return action(
                "finish",
                "finish",
                answer=analysis["interpretation"],
                evidence_ids=analysis["evidence_used"],
                limitations=analysis["limitations"],
            )

        return ScriptedLLMClient([first, second, third]), ControlledVLM()

    return AgentAPIRuntime(
        corpus, ControlledRetrieval(corpus), clients, retrieval_mode="test"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # 无界面的 Linux 可能缺少中文字形，截图时使用已有本地字体。
    # 不将专有字体复制进应用或用户产物。
    local_fonts = list(Path("/mnt/c/Windows/Fonts").glob("msyh*.ttc"))
    if local_fonts:
        font_dir = Path("/tmp/personal-agent-ui-fonts")
        font_dir.mkdir(exist_ok=True)
        for font in local_fonts:
            link = font_dir / font.name
            if not link.exists():
                link.symlink_to(font)
        config = font_dir / "fonts.conf"
        config.write_text(
            f'<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>{font_dir}</dir><cachedir>/tmp/personal-agent-font-cache</cachedir></fontconfig>',
            encoding="utf-8",
        )
        os.environ["FONTCONFIG_FILE"] = str(config)
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    prefix = f"{date}-多模态论文研究智能体-Day6"
    app = create_app(runtime_factory=make_test_runtime)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = monotonic() + 10
    while not server.started:
        if monotonic() > deadline:
            raise RuntimeError("UI test server did not start")
        sleep(0.05)
    checks = []
    screenshots = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1160, "height": 920})
            page_errors = []
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.goto(f"http://127.0.0.1:{port}/")
            assert page.locator("[data-example]").count() == 4
            for name in ["text", "figure", "recheck", "insufficient"]:
                page.locator(f'[data-example="{name}"]').click()
                assert page.locator("#question").input_value()
            checks.append("four example questions populate input without auto-running")
            page.locator('[data-example="text"]').click()
            page.locator("#ask-button").click()
            expect(page.locator("#answer")).to_contain_text("contrastive objective")
            expect(page.locator("#citations")).to_contain_text("CLIP · Page")
            assert page.locator("#trace .tool-name").all_text_contents() == [
                "search_text",
                "finish",
            ]
            assert page.locator("#metrics strong").all_text_contents()[:3] == [
                "1",
                "2",
                "0",
            ]
            checks.append(
                "text answer, page citation, chronological trace and metrics render through HTTP"
            )
            path = args.output_dir / f"{prefix}-TextQA页面-测试模型.png"
            page.screenshot(path=str(path), full_page=True)
            screenshots.append(str(path))
            page.locator('[data-example="figure"]').click()
            page.locator("#ask-button").click()
            expect(page.locator("#figures-section")).to_be_visible()
            expect(page.locator("#figures")).to_contain_text("BLIP · Page 4 · Figure 3")
            page.wait_for_function(
                'document.querySelector("#figures img")?.naturalWidth > 0'
            )
            expect(page.locator("#figures")).to_contain_text("Visual observations")
            expect(page.locator("#figures")).to_contain_text("Interpretation")
            assert page.locator("#trace .tool-name").all_text_contents() == [
                "find_figure",
                "analyze_figure",
                "finish",
            ]
            assert page.locator("#metrics strong").all_text_contents()[:3] == [
                "2",
                "3",
                "1",
            ]
            checks.append(
                "figure image, caption, observations/interpretation and VLM count render"
            )
            path = args.output_dir / f"{prefix}-FigureQA页面-测试模型.png"
            page.screenshot(path=str(path), full_page=True)
            screenshots.append(str(path))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            path = args.output_dir / f"{prefix}-移动端Figure页面-测试模型.png"
            page.screenshot(path=str(path), full_page=True)
            screenshots.append(str(path))
            checks.append("390px mobile layout has no horizontal overflow")
            page.locator('[data-example="insufficient"]').click()
            page.locator("#ask-button").click()
            expect(page.locator("#run-status")).to_have_text("证据不足")
            expect(page.locator("#answer")).to_contain_text(
                "does not contain enough evidence"
            )
            expect(page.locator("#citations")).to_contain_text("没有最终引用")
            expect(page.locator("#limitations-section")).to_be_visible()
            checks.append(
                "insufficient result displays answer, empty sources and limitations"
            )
            page.locator("#question").fill("HTML display check")
            page.locator("#ask-button").click()
            expect(page.locator("#answer")).to_contain_text("<script>")
            assert page.evaluate("window.__injected === undefined")
            assert page.locator("#answer script, #answer img").count() == 0
            checks.append("model answer is rendered as text, never executed as HTML")
            page.route(
                "**/api/agent/run",
                lambda route: route.fulfill(
                    status=500,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "status": "error",
                            "error": {
                                "code": "internal_error",
                                "message": "测试 API 错误",
                                "retryable": False,
                            },
                        }
                    ),
                ),
            )
            page.locator("#question").fill("Error display check")
            page.locator("#ask-button").click()
            expect(page.locator("#api-error")).to_have_text("测试 API 错误")
            expect(page.locator("#ask-button")).to_be_enabled()
            expect(page.locator("#result")).to_be_hidden()
            checks.append(
                "stable API error renders and submit button recovers without stale result"
            )
            assert not page_errors
            checks.append("no browser JavaScript errors")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    report = {
        "status": "passed",
        "backend": "fake model decisions and fake VLM over real API/runtime/corpus; not real semantic acceptance",
        "checks": checks,
        "screenshots": screenshots,
    }
    path = args.output_dir / f"{prefix}-浏览器界面验收.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(checks)} browser checks passed; {path}")


if __name__ == "__main__":
    main()
