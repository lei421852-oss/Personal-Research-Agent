"""真实浏览器检查主Agent工作台；使用Scripted模型与既有历史，禁止付费调用。"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from app.research.react_runtime import ReActWorkbenchRuntime
from app.research.run_manager import ResearchRunManager
from app.research.run_replay import ReplayRepository
from app.research.workbench_dependencies import ROOT
from scripts.check_chinese_workbench_ui import LocalService, configure_fonts
from tests.test_react_workbench import (
    CATALOG,
    PAPERS,
    Dependencies,
    PaperSearch,
    factory,
    finish,
    research_args,
    research_finish,
)


def manager_for(steps):
    deps = Dependencies()
    llm, vision, _, _, _ = factory(deps, steps)
    runtime = ReActWorkbenchRuntime(
        deps,
        llm_factory=llm,
        vision_factory=vision,
        paper_catalog=CATALOG,
        paper_retriever=PaperSearch(),
    )
    return ResearchRunManager(runtime, deps, ReplayRepository(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/react-workbench-ui-check")
    )
    parser.add_argument("--font-file", action="append", type=Path, default=[])
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    configure_fonts(args.font_file)
    checks, errors, examples, pictures = [], [], [], []
    question = "Compare BLIP-2 and Flamingo design evidence."
    scenarios = [
        ("direct", "Explain an evidence-grounded agent.", [finish()]),
        (
            "tools",
            question,
            [
                ("search_papers", {"query": question, "top_k": 2}),
                finish("metadata", paper_ids=PAPERS),
            ],
        ),
        (
            "multi_agent_research",
            question,
            [
                ("search_papers", {"query": question, "top_k": 2}),
                ("run_multi_agent_research", research_args(source="retrieved")),
                research_finish,
            ],
        ),
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for mode, question, steps in scenarios:
                with LocalService(manager_for(steps)) as url:
                    page = browser.new_page(viewport={"width": 1500, "height": 1000})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url)
                    expect(page.locator("#workflow .node")).to_have_count(0)
                    expect(page.locator("#workflow")).to_have_attribute(
                        "data-layout", "linear"
                    )
                    expect(page.locator("#papers input")).to_have_count(0)
                    expect(page.locator("#papers .paper-option")).to_have_count(25)
                    expect(page.locator("#start-button")).to_be_enabled()
                    assert page.locator("#start-button").bounding_box()["y"] < 900
                    page.locator("#question").fill(question)
                    with page.expect_response(
                        lambda r: r.url.endswith("/runs") and r.request.method == "POST"
                    ) as submitted:
                        page.locator("#start-button").click()
                    assert "paper_ids" not in submitted.value.request.post_data_json
                    identity = submitted.value.json()["run_id"]
                    expect(page.locator("#run-status")).to_have_text(
                        "已完成", timeout=20000
                    )
                    expect(page.locator("#direct-answer")).to_be_visible()
                    expect(page.locator("#direct-answer")).to_contain_text("中文展示")
                    assert page.locator(
                        "#workflow [data-node-type='MainAgent']"
                    ).count()
                    if mode != "multi_agent_research":
                        expect(page.locator("#workflow")).to_have_attribute(
                            "data-layout", "linear"
                        )
                        expect(page.locator("#workflow-layout")).to_have_text(
                            "单 Agent · 线性"
                        )
                        expect(
                            page.locator("#workflow [data-node-type='Worker']")
                        ).to_have_count(0)
                        expect(page.locator("#report-full-text")).to_have_count(0)
                    else:
                        expect(page.locator("#workflow")).to_have_attribute(
                            "data-layout", "tree"
                        )
                        expect(page.locator("#workflow-layout")).to_have_text(
                            "多 Agent 协作 · 树状"
                        )
                        expect(page.locator("#workflow .paper-branch")).to_have_count(2)
                        for paper_id in PAPERS:
                            branch = page.locator(
                                f'#workflow .paper-branch[data-paper-id="{paper_id}"]'
                            )
                            expect(
                                branch.locator("[data-node-type=Worker]")
                            ).to_have_count(1)
                            branch.locator("[data-node-type=Worker]").click()
                            expect(page.locator("#node-details")).to_contain_text(
                                paper_id
                            )
                        assert page.locator(
                            "#workflow [data-node-type='Worker']"
                        ).count()
                        assert page.locator(
                            "#workflow [data-node-type='Reviewer']"
                        ).count()
                        expect(page.locator("#report-full-text")).not_to_have_attribute(
                            "open", ""
                        )
                    if mode == "multi_agent_research":
                        records = page.request.get(
                            f"{url}/api/research/runs/{identity}/events"
                        ).json()["events"]
                        original_node_count = page.locator(
                            "#workflow button.node"
                        ).count()
                        handoff_index = next(
                            i
                            for i, e in enumerate(records)
                            if e["node_type"] == "ResearchTool"
                        )

                        def display_prefix(items, page=page):
                            page.evaluate(
                                "items => {state.events=items; rebuildNodes(); renderNodes();}",
                                items,
                            )

                        display_prefix(records[:handoff_index])
                        expect(page.locator("#workflow")).to_have_attribute(
                            "data-layout", "linear"
                        )
                        display_prefix(records[: handoff_index + 1])
                        expect(page.locator("#workflow")).to_have_attribute(
                            "data-layout", "tree"
                        )
                        expect(
                            page.locator("#workflow [data-node-type=Worker]")
                        ).to_have_count(0)
                        worker_index = next(
                            i
                            for i, e in enumerate(records)
                            if e["node_type"] == "Worker"
                            and e["details"].get("paper_id")
                        )
                        display_prefix(records[: worker_index + 1])
                        expect(page.locator("#workflow .paper-branch")).to_have_count(1)
                        display_prefix(records)
                        expect(page.locator("#workflow button.node")).to_have_count(
                            original_node_count
                        )
                        checks.append(
                            "协作调用触发线性转树状；角色按记录出现；首个Worker只出现一个论文分支；节点不重复。"
                        )
                    report = page.request.get(
                        f"{url}/api/research/runs/{identity}/report"
                    ).json()
                    assert report["execution_mode"] == mode
                    assert report["metrics"]["model_calls"] <= 200
                    path = args.output_dir / f"scripted-{mode}.png"
                    page.screenshot(path=str(path), full_page=True)
                    pictures.append(
                        {"path": path.name, "source": "scripted_domain_wiring"}
                    )
                    if mode == "multi_agent_research":
                        tree_path = args.output_dir / "scripted-collaboration-tree.png"
                        page.locator(".workflow-panel").screenshot(path=str(tree_path))
                        pictures.append(
                            {"path": tree_path.name, "source": "scripted_domain_wiring"}
                        )
                    examples.append({"path": mode, "report": report})
                    page.locator("#report-language-en").click()
                    expect(page.locator("#direct-answer")).to_contain_text(
                        "A direct answer"
                    )
                    page.reload()
                    expect(page.locator("#direct-answer")).to_be_visible(timeout=15000)
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.locator("body").evaluate(
                        "e=>e.scrollWidth<=innerWidth+1"
                    )
                    if mode == "multi_agent_research":
                        mobile_path = (
                            args.output_dir / "scripted-multi-agent-mobile.png"
                        )
                        page.screenshot(path=str(mobile_path), full_page=True)
                        pictures.append(
                            {
                                "path": mobile_path.name,
                                "source": "scripted_domain_wiring",
                            }
                        )
                    page.close()
                    checks.append(
                        f"{mode}: 无论文选择、实际角色、答案首屏、中英文、刷新恢复、窄屏布局。"
                    )
            with LocalService(manager_for([])) as url:
                page = browser.new_page(viewport={"width": 1500, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                for case in ["g2", "g2-1"]:
                    page.locator("#replay-select").select_option(case)
                    page.locator("#replay-button").click()
                    expect(page.locator("#run-status")).to_have_text(
                        "历史记录已载入", timeout=15000
                    )
                    expect(page.locator("#direct-answer")).to_be_visible()
                    expect(page.locator("#workflow")).to_have_attribute(
                        "data-layout", "tree"
                    )
                    path = args.output_dir / f"historical-{case}.png"
                    page.screenshot(path=str(path), full_page=True)
                    pictures.append(
                        {
                            "path": path.name,
                            "source": "existing_real_history_offline_replay",
                        }
                    )
                page.close()
                checks.append("G2/G2.1真实历史只读回放，原文和中文副本保留。")
        finally:
            browser.close()
    result = {
        "status": "PASS" if not errors else "FAIL",
        "checks": checks,
        "page_errors": errors,
        "screenshots": pictures,
        "real_provider_calls": 0,
        "new_real_research_runs": 0,
        "embedding_calls": 0,
        "index_rebuilds": 0,
        "semantic_quality_verified": False,
    }
    (args.output_dir / "browser_check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    (args.output_dir / "scripted_path_examples.json").write_text(
        json.dumps(examples, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
