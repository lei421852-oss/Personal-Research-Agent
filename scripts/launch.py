"""统一启动工作台；检查依赖或启动本地服务，均不自动执行研究任务。"""

import argparse
import importlib.util
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("工作台第一版只允许绑定本机地址。")
    if not 1 <= args.port <= 65535:
        parser.error("端口必须处于 1–65535。")
    from app.research.workbench_dependencies import WorkbenchDependencies

    health = WorkbenchDependencies().health()
    if args.check:
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return 0 if health["ui_ready"] else 2
    missing = [
        module
        for module in ("fastapi", "uvicorn", "pydantic")
        if importlib.util.find_spec(module) is None
    ]
    if missing or not health["ui_ready"]:
        print("工作台无法启动：缺少必要 Python 依赖或静态资源，请查看安装指南。")
        return 2
    import uvicorn

    address = "[::1]" if args.host == "::1" else args.host
    print(f"工作台地址：http://{address}:{args.port}", flush=True)
    print(
        "启动预检："
        + json.dumps(
            {
                name: health[name]
                for name in ("ui_ready", "replay_ready", "real_research_ready")
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not health["real_research_ready"]:
        print("真实研究依赖未就绪，仍可打开工作台并查看已有历史回放。", flush=True)

    uvicorn.run(
        "app.workbench:create_app", host=args.host, port=args.port, factory=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
