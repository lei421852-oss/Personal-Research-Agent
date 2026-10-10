"""验证中文动态工作台：真实历史回放与 Fake Provider 领域链，禁止付费调用。"""

import argparse
import importlib.util
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
from app.workbench import create_app
from scripts.check_workbench_ui import ForbiddenResearchRunner, ReplayOnlyDependencies

ROOT = Path(__file__).resolve().parents[1]


class LocalService:
    """只绑定临时本机端口，每段检查后等待应用实际完成收尾。"""

    def __init__(self, manager):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_app(manager_factory=lambda: manager), log_level="error"
            )
        )
        self.thread = Thread(
            target=self.server.run, kwargs={"sockets": [sock]}, daemon=True
        )

    def __enter__(self):
        self.thread.start()
        deadline = monotonic() + 10
        while not self.server.started:
            if monotonic() > deadline:
                raise RuntimeError("浏览器检查服务未启动。")
            sleep(0.02)
        return self.url

    def __exit__(self, *args):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("浏览器检查服务未收尾。")


def test_factory():
    """复用同一套确定性集成夹具，避免再复制一份研究执行业务。"""
    spec = importlib.util.spec_from_file_location(
        "chinese_workbench_browser_fixtures",
        ROOT / "tests/test_chinese_workbench_runtime.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fake_runtime


def configure_fonts(fonts):
    if not fonts:
        return
    folder = Path("/tmp/chinese-workbench-fonts")
    folder.mkdir(exist_ok=True)
    for index, font in enumerate(fonts):
        if not font.is_file():
            raise ValueError("指定字体不存在。")
        link = folder / f"{index}-{font.name}"
        if not link.exists():
            link.symlink_to(font.resolve())
    config = folder / "fonts.conf"
    config.write_text(
        '<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        f"<dir>{folder}</dir><cachedir>/tmp/chinese-workbench-font-cache</cachedir></fontconfig>"
    )
    os.environ["FONTCONFIG_FILE"] = str(config)


def screenshot(page, output, name, pictures, source):
    path = output / name
    page.screenshot(path=str(path), full_page=True)
    pictures.append({"path": str(path), "source": source})


def replay_check(browser, output, checks, pictures, errors):
    forbidden = ForbiddenResearchRunner()
    manager = ResearchRunManager(
        forbidden, ReplayOnlyDependencies(), ReplayRepository(ROOT)
    )
    historical = []
    with LocalService(manager) as url:
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request", lambda request: requests.append((request.method, request.url))
        )
        page.goto(url)
        expect(page.locator("#papers input")).to_have_count(5)
        expect(page.locator("#papers input:checked")).to_have_count(0)
        expect(page.locator("#workflow .node")).to_have_count(0)
        expect(page.locator("#start-button")).to_be_disabled()
        expect(page.locator("#replay-button")).to_be_enabled()
        expect(page.locator("#selection-mode")).to_contain_text("自动")
        grid = page.locator(".workbench").evaluate(
            "e => getComputedStyle(e).gridTemplateColumns.split(' ').length"
        )
        assert grid == 3
        assert page.locator("body").evaluate(
            "e => { const c=getComputedStyle(e).backgroundColor.match(/\\d+/g).map(Number); return c[0]===c[1] && c[1]===c[2]; }"
        )
        screenshot(
            page, output, "chinese-workbench-initial.png", pictures, "real_ui_no_run"
        )
        checks.append("桌面三栏黑白灰页面，初始零节点、零勾选，缺Provider仍可回放。")

        # 服务数据仍来自同一个历史文件；只乱序并重复响应，检验浏览器恢复算法。
        def shuffled_events(route):
            response = route.fetch()
            body = response.json()
            events = body.get("events", [])
            body["events"] = list(reversed(events)) + events[:2]
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )

        page.route("**/api/research/runs/*/events?*", shuffled_events)
        for case, verified, missing in (("g2", 5, 7), ("g2-1", 4, 10)):
            page.select_option("#replay-select", case)
            page.locator("#replay-button").click()
            expect(page.locator("#mode-badge")).to_contain_text("历史回放")
            expect(page.locator("#report-details .claim-card")).to_have_count(verified)
            expect(page.locator("#report-details .missing-card")).to_have_count(missing)
            expect(page.locator("#report-language-notice")).to_contain_text(
                "中文阅读版"
            )
            expect(page.locator("#report-panel")).to_be_visible()
            expect(page.locator("#workflow [data-node-id^='worker:']")).to_have_count(2)
            assert page.locator("#workflow [data-node-type='Recovery']").count() == 0
            assert not page.locator("#validator-nodes .node-time").count()
            ids = page.locator("#event-list li[data-event-id]").evaluate_all(
                "items => items.map(e => Number(e.dataset.eventId))"
            )
            assert len(ids) == len(set(ids)) and ids == sorted(ids)
            historical.append(
                {
                    "case_id": case,
                    "verified_claims": verified,
                    "missing_requirements": missing,
                    "events": len(ids),
                }
            )
            if case == "g2":
                page.locator("#workflow .node.failed").first.click()
                expect(page.locator("#node-details")).to_contain_text("执行失败")
                checks.append(
                    "G2原Worker协议失败可查看，历史失败没有被中文版改为成功。"
                )
            screenshot(
                page,
                output,
                f"chinese-{case}-replay.png",
                pictures,
                "historical_replay_offline_translation",
            )
            page.locator("#report-language-en").click()
            expect(page.locator("#report-language-notice")).to_contain_text("英文权威")
            page.locator("#report-language-zh").click()
            checks.append(
                f"{case}中文离线回放保留{verified}事实/{missing}缺口，原文可切换，无未执行Recovery节点。"
            )

        page.locator("#workflow .node").first.click()
        expect(page.locator("#node-details")).not_to_contain_text("等待节点记录")
        expect(page.locator("#report-panel")).to_be_visible()
        page.locator("#report-details .claim-card .text-button").first.click()
        expect(page.locator("#evidence-panel")).to_be_visible()
        expect(page.locator("#evidence-details")).to_contain_text("PDF 第")
        assert page.locator("#evidence-details .evidence-id").count()
        checks.append(
            "节点过程详情、原论文引用页码和正文可查看，最终报告独立保持可见。"
        )

        posts = sum(method == "POST" for method, _ in requests)
        count = page.locator("#event-list li[data-event-id]").count()
        page.reload()
        expect(page.locator("#report-details .claim-card")).to_have_count(4)
        expect(page.locator("#event-list li[data-event-id]")).to_have_count(count)
        assert sum(method == "POST" for method, _ in requests) == posts
        checks.append("乱序/重复事件去重排序，刷新同run恢复不新增POST。")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot(
            page,
            output,
            "chinese-mobile-replay.png",
            pictures,
            "historical_replay_offline_translation",
        )
        checks.append("390px窄屏上下布局无横向溢出。")
        page.set_viewport_size({"width": 1500, "height": 1000})

        def failed_translation(route):
            response = route.fetch()
            body = response.json()
            body["language"] = {
                "status": "failed",
                "issue_code": "controlled_translation_failure",
            }
            body["chinese_report"] = None
            body["markdown"] = (
                '<script>window.__injected=true</script><img src=x onerror="window.__injected=true">'
            )
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )

        page.route("**/api/research/runs/*/report", failed_translation)
        page.reload()
        expect(page.locator("#report-language-notice")).to_contain_text("回退")
        page.locator("#tab-report").click()
        expect(page.locator("#report-details .report-text")).to_contain_text("<script>")
        assert page.evaluate("window.__injected === undefined")
        assert not page.locator("#report-details script, #report-details img").count()
        checks.append("受控翻译失败保留原报告，HTML作为文本展示不执行。")
        assert forbidden.calls == 0
        assert not any(
            method == "POST" and endpoint.endswith("/runs")
            for method, endpoint in requests
        )
        page.close()
    return historical


def fake_pipeline_check(browser, output, checks, pictures, errors):
    runtime, dependencies, created = test_factory()(reject_initial=True)
    manager = ResearchRunManager(runtime, dependencies, ReplayRepository(ROOT))
    with LocalService(manager) as url:
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.locator("#papers input:checked")).to_have_count(0)
        expect(page.locator("#start-button")).to_be_enabled()
        # 单选明确无效，取消勾选恢复自动模式；不代用户扩大范围。
        page.locator("#papers input").first.check()
        expect(page.locator("#start-button")).to_be_disabled()
        page.locator("#papers input").nth(1).check()
        expect(page.locator("#start-button")).to_be_enabled()
        page.locator("#papers input").first.uncheck()
        page.locator("#papers input").nth(1).uncheck()
        page.locator("#question").fill(
            "比较 BLIP-2 和 Flamingo 的视觉语言连接机制，并引用证据。"
        )
        with page.expect_response(
            lambda response: (
                response.url.endswith("/runs") and response.request.method == "POST"
            )
        ) as submitted:
            page.locator("#start-button").click()
        assert submitted.value.request.post_data_json["paper_ids"] == []
        identity = submitted.value.json()["run_id"]
        expect(page.locator("#run-status")).to_contain_text("已完成", timeout=20000)
        expect(page.locator("#report-language-notice")).to_contain_text("中文阅读版")
        expect(page.locator("#report-details .claim-card")).to_have_count(2)
        data = page.request.get(f"{url}/api/research/runs/{identity}/report").json()
        status = page.request.get(f"{url}/api/research/runs/{identity}").json()
        assert status["paper_ids"] and len(status["paper_ids"]) == 2
        assert data["language"]["status"] == "translated"
        for role in (
            "QueryLanguage",
            "PaperSelection",
            "Planner",
            "Worker",
            "Reviewer",
            "Recovery",
            "Synthesis",
            "ReportLanguage",
        ):
            assert page.locator(f"#workflow [data-node-type='{role}']").count(), role
        assert len(data["verified_claims"]) == 2
        assert data["diagnostics"]["recovery"]["recovery_rounds_used"] == 1
        screenshot(
            page,
            output,
            "chinese-fake-auto-pipeline.png",
            pictures,
            "fake_provider_default_domain_pipeline",
        )
        checks.append(
            "Fake Provider中文无勾选请求真正进入原Planner/默认Worker/Reviewer/一轮Recovery/Synthesis并形成合法报告。"
        )
        checks.append(
            "真实页面支持手选范围，单选明确提示；自动选择及中文展示调用计入同一预算。"
        )
        page.close()
        return {
            "run_id": identity,
            "status": status["status"],
            "paper_ids": status["paper_ids"],
            "metrics": data["metrics"],
            "verified_claims": len(data["verified_claims"]),
            "language_status": data["language"]["status"],
            "fake_clients": len(created),
            "independent_semantic_quality_verified": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/chinese-workbench-ui-check")
    )
    parser.add_argument("--font-file", action="append", type=Path, default=[])
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    configure_fonts(args.font_file)
    checks, pictures, errors = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            historical = replay_check(
                browser, args.output_dir, checks, pictures, errors
            )
            simulated = fake_pipeline_check(
                browser, args.output_dir, checks, pictures, errors
            )
        finally:
            browser.close()
    assert not errors
    result = {
        "status": "PASS",
        "verification_type": "real_browser_historical_replay_and_fake_provider_domain_integration",
        "real_provider_calls": 0,
        "real_research_quality_runs": 0,
        "embedding_calls": 0,
        "index_rebuilds": 0,
        "checks": checks,
        "history_cases": historical,
        "fake_provider_pipeline": simulated,
        "page_errors": errors,
        "screenshots": pictures,
    }
    (args.output_dir / "chinese-workbench-ui-check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
