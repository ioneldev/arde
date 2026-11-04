"""Run folders laid out as Workspace makes them, for the tests of finding and exporting runs."""

import json
from pathlib import Path

RUN_ID = "20261104T105708Z-a8063e"
WORKSPACE = "things-0123456789ab"
# input, output: the file in each copy, None when that copy does not have it.
CHANGES: dict[str, tuple[bytes | None, bytes | None]] = {
    "main.go": (
        b"package main\r\n\r\nfunc main() {}\r\n",
        b"package main\r\n\r\n// @Router\r\nfunc main() {}\r\n",
    ),
    "docs/api.md": (None, b"# API\n"),
}


def a_run(
    home: Path,
    *,
    run_id: str = RUN_ID,
    workspace: str = WORKSPACE,
    report: dict[str, object] | None = None,
    source_kind: str = "local",
    changes: dict[str, tuple[bytes | None, bytes | None]] = CHANGES,
) -> Path:
    run_dir = home / "workspaces" / workspace / "runs" / run_id
    for copy, side in (("input", 0), ("output", 1)):
        for name, contents in changes.items():
            content = contents[side]
            if content is not None:
                path = run_dir / copy / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    (run_dir / "trace").mkdir(parents=True, exist_ok=True)
    start = {"kind": "run_start", "source": "https://example.com/team/things.git"}
    (run_dir / "trace" / "trace.jsonl").write_text(
        json.dumps({**start, "source_kind": source_kind}) + "\n"
    )
    (run_dir / "trace" / "debug.log").write_text("the model saw this\n")
    (run_dir / "service-after.log").write_text("listening\n")
    shown = {
        "status": "success",
        "exit_code": 0,
        "cost_usd": 0.7634,
        "duration_s": 93.2,
        "branch": None,
        "changed_paths": list(changes),
    }
    (run_dir / "report.json").write_text(json.dumps(shown if report is None else report))
    return run_dir
