"""Representative server timing matrix for local-smartz research requests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from localsmartz.benchmarking import (
    _free_port,
    measure_research_request,
    spawn_server,
    summarize_runs,
)
from localsmartz.routing import select_research_runtime


@dataclass(frozen=True)
class ServerTimingCase:
    name: str
    prompt: str
    expected_runtime: str


SERVER_TIMING_CASES: tuple[ServerTimingCase, ...] = (
    ServerTimingCase("fast_math", "what is 2+2?", "fast_path"),
    ServerTimingCase(
        "current_data",
        "what is the latest price of Apple stock?",
        "graph_pipeline",
    ),
    ServerTimingCase(
        "comparison",
        "compare Python and Rust for a backend API",
        "graph_pipeline",
    ),
)


def run_server_timing_matrix(
    *,
    base_url: str,
    cwd: str,
    cases: Iterable[ServerTimingCase] = SERVER_TIMING_CASES,
    runs: int = 1,
    stop_after: str = "first_text",
    profile: str | None = None,
    agent: str | None = None,
    timeout_s: float = 120.0,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for case in cases:
        measured = [
            measure_research_request(
                base_url,
                prompt=case.prompt,
                run_index=idx + 1,
                profile=profile,
                agent=agent,
                cwd=cwd,
                timeout_s=timeout_s,
                stop_after=stop_after,  # type: ignore[arg-type]
            )
            for idx in range(runs)
        ]
        summary = summarize_runs(measured)
        actual_runtime = select_research_runtime(case.prompt, focus_agent=agent)
        rows.append(
            {
                "name": case.name,
                "prompt": case.prompt,
                "expected_runtime": case.expected_runtime,
                "actual_runtime": actual_runtime,
                "runtime_ok": actual_runtime == case.expected_runtime,
                "summary": summary,
                "runs": [asdict(run) for run in measured],
            }
        )

    return {
        "base_url": base_url,
        "run_count_per_case": runs,
        "stop_after": stop_after,
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="localsmartz-server-timing",
        description="Measure representative /api/research server timings.",
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--stop-after",
        choices=("done", "first_stage", "first_text"),
        default="first_text",
    )
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--disable-role-cache", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    workspace = str(Path(args.cwd).resolve())
    cases = list(SERVER_TIMING_CASES)
    if args.limit is not None:
        cases = cases[: max(1, args.limit)]

    base_url = args.base_url
    process: subprocess.Popen | None = None
    log_path: str | None = None
    startup_ms: int | None = None
    try:
        if base_url is None:
            port = args.port or _free_port()
            process, startup_ms, log_path = spawn_server(
                cwd=Path(workspace),
                port=port,
                python_executable=args.python,
                profile=args.profile,
                observe=args.observe,
                disable_role_cache=args.disable_role_cache,
            )
            base_url = f"http://127.0.0.1:{port}"

        payload = run_server_timing_matrix(
            base_url=base_url,
            cwd=workspace,
            cases=cases,
            runs=max(1, args.runs),
            stop_after=args.stop_after,
            profile=args.profile,
            agent=args.agent,
            timeout_s=args.timeout,
        )
        payload["spawned_server"] = process is not None
        payload["startup_ms"] = startup_ms
        payload["log_path"] = log_path
        if args.output:
            Path(args.output).write_text(json.dumps(payload, indent=2))
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for row in payload["cases"]:  # type: ignore[index]
                summary = row["summary"]
                print(
                    f"{row['name']}: runtime={row['actual_runtime']} "
                    f"first_text={summary.get('median_first_text_ms')}ms "
                    f"wall={summary.get('median_wall_ms')}ms"
                )
        return 0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
