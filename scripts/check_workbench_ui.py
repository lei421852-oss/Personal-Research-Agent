"""浏览器验证工作台真实历史回放；显式禁用真实研究，不调用 Provider。"""

import argparse
import json
import os
import socket
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import uvicorn
from playwright.sync_api import expect, sync_playwright

from app.research.run_manager import ResearchRunManager
from app.research.run_replay import ReplayRepository
from app.research.workbench_dependencies import WorkbenchDependencies
from app.workbench import create_app

ROOT = Path(__file__).resolve().parents[1]


class ReplayOnlyDependencies:
    """语料来自实际工程；模型依赖被显式阻断，避免浏览器验证产生付费调用。"""

    def __init__(self):
        self.prepared = WorkbenchDependencies(ROOT)

    def papers(self):
        return self.prepared.papers()

    def health(self):
        return {
            "ui_ready": True,
            "replay_ready": True,
            "real_research_ready": False,
            "dependencies": [
                {
                    "name": "Prepared Corpus",
                    "status": "ready",
                    "message": "读取当前实际预处理论文目录。",
                },
                {
                    "name": "Provider",
                    "status": "blocked",
                    "message": "历史回放验证显式禁用真实模型调用。",
                },
            ],
            "limits": {"max_model_calls": 200, "max_concurrent_runs": 1},
        }


class ForbiddenResearchRunner:
    """一旦浏览器错误触发真实研究，验证就失败，而非偷偷调用模型。"""

    def __init__(self):
        self.calls = 0

    async def run(self, request, emit):
        self.calls += 1
        raise AssertionError("浏览器回放验证禁止执行真实研究。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/tmp/workbench-ui-check")
    )
    parser.add_argument(
        "--font-file",
        action="append",
        type=Path,
        default=[],
        help="截图可复用的本地中文字体，不复制进仓库。",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.font_file:
        # 临时字体目录仅链接调用者提供的文件，避免把字体或缓存混入交付目录。
        font_dir = Path("/tmp/workbench-ui-fonts")
        font_dir.mkdir(exist_ok=True)
        for index, font in enumerate(args.font_file):
            if not font.is_file():
                raise ValueError("指定的截图字体不存在。")
            link = font_dir / f"{index}-{font.name}"
            if not link.exists():
                link.symlink_to(font.resolve())
        font_config = font_dir / "fonts.conf"
        font_config.write_text(
            '<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
            f"<dir>{font_dir}</dir><cachedir>/tmp/workbench-ui-font-cache</cachedir></fontconfig>",
            encoding="utf-8",
        )
        os.environ["FONTCONFIG_FILE"] = str(font_config)
    runner = ForbiddenResearchRunner()
    manager = ResearchRunManager(
        runner, ReplayOnlyDependencies(), ReplayRepository(ROOT)
    )
    app = create_app(manager_factory=lambda: manager)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = monotonic() + 10
    while not server.started:
        if monotonic() > deadline:
            raise RuntimeError("浏览器验证服务未能启动。")
        sleep(0.05)
    checks, screenshots, page_errors, requests = [], [], [], []
    replay_sources = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 1060})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "request",
                lambda request: requests.append(
                    {"method": request.method, "url": request.url}
                ),
            )
            page.goto(f"http://127.0.0.1:{port}/")
            expect(page.locator("#papers input")).to_have_count(5)
            expect(page.locator("#papers input:checked")).to_have_count(2)
            expect(page.locator("#start-button")).to_be_disabled()
            expect(page.locator("#replay-button")).to_be_enabled()
            assert not any(
                item["method"] == "POST" and item["url"].endswith("/runs")
                for item in requests
            )
            checks.append("初始页面不启动研究；Provider 缺失时禁用提交但允许回放。")

            for identity, verified, missing in [("g2", 5, 7), ("g2-1", 4, 10)]:
                page.select_option("#replay-select", identity)
                page.locator("#replay-button").click()
                expect(page.locator("#mode-badge")).to_contain_text("历史回放")
                expect(page.locator("#report-details .claim-card")).to_have_count(
                    verified
                )
                expect(page.locator("#report-details .missing-card")).to_have_count(
                    missing
                )
                expect(page.locator("#worker-nodes .node")).to_have_count(2)
                assert page.locator("#validator-nodes .node-time").count() == 0
                assert set(page.locator(".event-source").all_text_contents()) == {
                    "历史回放"
                }
                replay_sources.append(
                    {
                        "case_id": identity,
                        "verified_claims": verified,
                        "missing_requirements": missing,
                        "event_count": page.locator("#event-list li").count(),
                    }
                )
                if identity == "g2":
                    expect(page.locator("#worker-nodes .node.failed")).to_have_count(1)
                    page.locator("#worker-nodes .node.failed").click()
                    expect(page.locator("#node-details")).to_contain_text("执行失败")
                    path = args.output_dir / "workbench-g2-failure-details.png"
                    page.evaluate("window.scrollTo(0, 0)")
                    page.screenshot(path=str(path), full_page=True)
                    screenshots.append(str(path))
                else:
                    expect(page.locator("#worker-nodes .node.completed")).to_have_count(
                        2
                    )
                    expect(page.locator("#reviewer-nodes")).to_contain_text("跳过 10")
                    expect(page.locator("#reviewer-nodes .node.failed")).to_have_count(
                        0
                    )
                page.locator("#tab-report").click()
                path = args.output_dir / f"workbench-{identity}-replay.png"
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(path), full_page=True)
                screenshots.append(str(path))
                checks.append(
                    f"{identity} 真实历史记录：{verified} 条已审核论断、{missing} 个缺失单元；Worker 状态和事件来源正确。"
                )

            page.locator("#report-details .claim-card .text-button").first.click()
            expect(page.locator("#evidence-panel")).to_be_visible()
            expect(page.locator("#evidence-details")).to_contain_text("PDF 第")
            assert page.locator("#evidence-details .evidence-id").count() > 0
            path = args.output_dir / "workbench-g2-1-evidence.png"
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(path), full_page=True)
            screenshots.append(str(path))
            checks.append("报告引用可切换到真实页码、证据正文和对应论断。")

            before_reload = len([item for item in requests if item["method"] == "POST"])
            event_count = page.locator("#event-list li").count()
            page.reload()
            expect(page.locator("#report-details .claim-card")).to_have_count(4)
            expect(page.locator("#event-list li")).to_have_count(event_count)
            assert before_reload == len(
                [item for item in requests if item["method"] == "POST"]
            )
            event_ids = page.locator("#event-list li").evaluate_all(
                "items => items.map(item => item.dataset.eventId)"
            )
            assert len(set(event_ids)) == len(event_ids)
            checks.append("刷新恢复同一 run_id；不会重复创建任务或重复展示事件。")

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            path = args.output_dir / "workbench-mobile-replay.png"
            page.screenshot(path=str(path), full_page=True)
            screenshots.append(str(path))
            checks.append("390px 移动布局无横向溢出。")
            page.set_viewport_size({"width": 1500, "height": 1060})

            # 仅在测试页面拦截公开报告响应，检验 HTML 防注入；不修改原始历史文件。
            def inject_report(route):
                response = route.fetch()
                body = response.json()
                body["markdown"] = (
                    "<script>window.__workbench_injected=true</script>"
                    '<img src=x onerror="window.__workbench_injected=true">'
                )
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(body, ensure_ascii=False),
                )

            page.route("**/api/research/runs/*/report", inject_report)
            page.reload()
            expect(page.locator("#report-details .report-text")).to_contain_text(
                "<script>"
            )
            assert page.evaluate("window.__workbench_injected === undefined")
            assert (
                page.locator("#report-details script, #report-details img").count() == 0
            )
            checks.append("报告通过 textContent 展示，HTML/脚本不会执行。")

            assert not page_errors
            assert runner.calls == 0
            assert not any(
                item["method"] == "POST" and item["url"].endswith("/runs")
                for item in requests
            )
            checks.append("没有浏览器异常；真实研究、Provider 与检索调用均为零。")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("浏览器验证服务未完成关闭。")
    result = {
        "status": "PASS",
        "verification_type": "real_historical_replay_browser_check",
        "provider_readiness": "explicitly_disabled_for_browser_check",
        "real_provider_calls": 0,
        "real_research_calls": runner.calls,
        "retrieval_calls": 0,
        "checks": checks,
        "history_cases": replay_sources,
        "page_errors": page_errors,
        "screenshots": screenshots,
    }
    output = args.output_dir / "workbench-ui-check.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
