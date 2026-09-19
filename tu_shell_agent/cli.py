"""无界面的开发驱动：跑完整流程并打印时间线。

用法：
  .venv/bin/python -m tu_shell_agent.cli --plan test_fixtures/plan-simple.md --template single --run-root /tmp/tu-runs --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .opencode_adapter import OpencodeAdapter
from .orchestrator.contract import extract_anchors
from .orchestrator.loop import LoopInput, LoopPorts, TemplateSpec, run_loop
from .run_store.layout import make_run_id, run_dir_for
from .run_store.store import RunStore
from .shell_toolchain.detect import detect_all, system_deps
from .shell_toolchain.facade import ShellToolchain
from .template_store.render import render_template
from .ui.settings import default_templates_dir
from .template_store.store import TemplateStore
from .types import RunConfig, RunEvent


def _path_overrides(args: argparse.Namespace) -> dict[str, str]:
    """把 CLI 的 --*-path 覆盖转成 detect_all 认的 {tool: path} 映射。

    必须 resolve() 成绝对路径：server.start_serve 用 cwd=run_dir 起 serve 子进程、
    execute.run_script 也用 cwd=run_dir 执行脚本，**相对路径会在新 cwd 下解析不到**。
    实测：`--opencode-path tools/opencode` → `[Errno 2] No such file or directory: 'tools/opencode'`
    → aborted_dependency(0 轮)，而 CLI 自己的自检却是通过的（现象很迷惑）。

    expanduser() 也不能少：`--opencode-path '~/tools/opencode'` 不展开的话会 resolve 成
    字面 `cwd/~/tools/opencode`，探测失败后**静默回退 PATH** —— 用户以为覆盖生效了，其实没有。
    """
    pairs = (
        ("opencode", args.opencode_path),
        ("bash", args.bash_path),
        ("shellcheck", args.shellcheck_path),
    )
    return {tool: str(Path(path).expanduser().resolve()) for tool, path in pairs if path}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tu_shell_agent.cli", description="方案 → shell 脚本 → 执行 → 校验")
    parser.add_argument("--plan", required=True, help="方案文档路径")
    parser.add_argument("--template", default="single", help="模板 id（默认 single）")
    parser.add_argument("--run-root", default=str(Path.cwd() / ".tu-runs"))
    parser.add_argument("--templates-dir", default=str(default_templates_dir()))
    parser.add_argument("--opencode-path", default=None, help="覆盖 opencode 可执行文件路径（默认自动探测）")
    parser.add_argument("--bash-path", default=None, help="覆盖 bash 可执行文件路径（默认自动探测）")
    parser.add_argument("--shellcheck-path", default=None, help="覆盖 shellcheck 可执行文件路径（默认自动探测）")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--generate-timeout-ms", type=int, default=300_000)
    parser.add_argument("--execute-timeout-ms", type=int, default=120_000)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="非交互环境必须显式加这个才会执行生成的脚本（规格 §11）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    overrides = _path_overrides(args)
    report = detect_all(system_deps(overrides))
    print("环境自检：", report, flush=True)
    for warning in report.warnings:
        # 提示不阻断：它描述的是一种"大概率失败"的配置，而不是确定的故障
        # （例如 opencode 没存凭据，但用户可能用环境变量给了 API key）。
        print(f"提示：{warning}", file=sys.stderr, flush=True)
    if report.problems:
        print("自检未通过：\n" + "\n".join(report.problems), file=sys.stderr)
        return 2
    assert report.opencode and report.bash and report.shellcheck

    store = TemplateStore(args.templates_dir)
    meta = store.get(args.template)
    body = store.read(args.template)
    skeleton = render_template(body, meta.placeholders, {})
    anchors = extract_anchors(skeleton)

    run_dir = run_dir_for(args.run_root, make_run_id())
    run_store = RunStore(run_dir)
    run_store.init()

    config = RunConfig(
        run_root=args.run_root,
        max_rounds=args.max_rounds,
        generate_timeout_ms=args.generate_timeout_ms,
        execute_timeout_ms=args.execute_timeout_ms,
        opencode_path=report.opencode.path,
        bash_path=report.bash.path,
        shellcheck_path=report.shellcheck.path,
    )
    adapter = OpencodeAdapter(
        opencode_path=report.opencode.path,
        note=lambda message: print(f"[opencode] {message}", flush=True),
    )

    def confirm(round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        print(f"\n===== 第 {round_no} 轮脚本，即将执行 =====\n{script}", flush=True)
        if args.yes:
            return True
        if not sys.stdin.isatty():
            print("非交互环境：默认不执行。确认要执行请加 --yes。", file=sys.stderr)
            return False
        answer = input("执行？[y/N] ").strip().lower()
        return answer == "y"

    def emit(event: RunEvent) -> None:
        if event.type == "phase":
            print(f"[轮 {event.round}] {event.payload.get('phase')}", flush=True)
        elif event.type == "assistant_delta":
            print(event.payload.get("text", ""), end="", flush=True)
        elif event.type == "shellcheck":
            print(f"\nshellcheck：{len(event.payload.get('findings') or ())} 条", flush=True)
        elif event.type == "execute":
            result = event.payload["result"]
            print(
                f"\n执行退出码 {result.exit_code}（{result.duration_ms}ms）\n{result.stdout}",
                flush=True,
            )
        elif event.type == "note":
            print(f"\n[note] {event.payload.get('message')}", flush=True)

    ports = LoopPorts(
        opencode=adapter,
        # 必须把同一组 override 一起传进 facade：run_loop 会再调一次 toolchain.detect()，
        # 少了 override 就会在"shellcheck 不在 PATH"的机器上把已解析出的路径又判成缺失 →
        # 循环内预检失败 → aborted_dependency（CLI 自己的自检却通过了，现象很迷惑）。
        toolchain=ShellToolchain(report.bash.path, report.shellcheck.path, overrides),
        confirm=type("CliConfirm", (), {"confirm": staticmethod(confirm)})(),
        store=run_store,
        emit=emit,
    )

    try:
        result = run_loop(
            LoopInput(
                plan=Path(args.plan).read_text(encoding="utf-8"),
                template=TemplateSpec(
                    id=args.template,
                    body=body,
                    anchors=anchors,
                    trusted=False,
                    placeholders=tuple(meta.placeholders),
                ),
                values={},
                run_dir=run_dir,
                config=config,
                ports=ports,
            )
        )
        print(f"\n结论：{result.outcome}（{result.rounds} 轮）\n运行目录：{run_dir}")
        return 0 if result.outcome == "succeeded" else 1
    finally:
        adapter.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
