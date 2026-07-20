#!/usr/bin/env python3
# Smoke test validate.py trên fixtures: mỗi case fail phải cho ĐÚNG 1 dòng lỗi với rule
# kỳ vọng; case pass exit 0. Chạy: ~/.claude/skills/.venv/bin/python3 tests/run_smoke.py

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALIDATE = ROOT / "scripts" / "validate.py"

# (fixture, stage, expected_rule | None nếu pass, expected_exit)
CASES = [
    ("content-pass", "content", None, 0),
    ("content-fail-missing-file", "content", "content-missing", 1),
    ("content-fail-empty-description", "content", "schema", 1),
    ("content-fail-bad-type", "content", "schema", 1),
    ("content-warn-status-drift", "content", None, 0),
    ("script-pass", "script", None, 0),
    ("script-fail-missing-index", "script", "index-missing", 1),
    ("script-fail-duplicate-index", "script", "index-duplicate", 1),
    ("script-fail-empty-narration", "script", "narration-empty", 1),
    ("script-fail-bad-type", "script", "schema", 1),
    ("script-fail-out-of-range", "script", "index-out-of-range", 1),
    ("series-pass", "series", None, 0),
    ("series-fail-missing-summary", "series", "summary-missing", 1),
    ("series-fail-bad-schema", "series", "schema", 1),
    ("series-fail-recap-ref", "series", "recap-ref-unknown", 1),
    ("series-warn-bible-over-cap", "series", None, 0),
]


def main() -> None:
    subprocess.run([sys.executable, str(Path(__file__).parent / "make_fixtures.py")], check=True, capture_output=True)
    failures = []
    for fixture, stage, rule, expected_exit in CASES:
        # Stage "series" dùng CLI --series (fixture là thư mục .mtv); còn lại --workdir/--stage
        if stage == "series":
            cli = [sys.executable, str(VALIDATE), "--series", str(FIXTURES / fixture)]
        else:
            cli = [sys.executable, str(VALIDATE), "--workdir", str(FIXTURES / fixture), "--stage", stage]
        proc = subprocess.run(cli, capture_output=True, text=True)
        errors = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
        label = f"{fixture}[{stage}]"
        if proc.returncode != expected_exit:
            failures.append(f"{label}: exit {proc.returncode}, kỳ vọng {expected_exit}. stdout={proc.stdout!r} stderr={proc.stderr!r}")
        elif rule is None and errors:
            failures.append(f"{label}: kỳ vọng pass nhưng có lỗi: {errors}")
        elif rule is not None:
            if len(errors) != 1:
                failures.append(f"{label}: kỳ vọng đúng 1 lỗi, nhận {len(errors)}: {errors}")
            elif errors[0]["rule"] != rule:
                failures.append(f"{label}: rule '{errors[0]['rule']}', kỳ vọng '{rule}'")
        if fixture == "content-warn-status-drift" and "status-drift" not in proc.stderr:
            failures.append(f"{label}: thiếu warning status-drift trên stderr")
        if fixture == "series-warn-bible-over-cap" and "bible-over-cap" not in proc.stderr:
            failures.append(f"{label}: thiếu warning bible-over-cap trên stderr")

    if failures:
        print("SMOKE FAIL:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"SMOKE PASS: {len(CASES)} case OK")


if __name__ == "__main__":
    main()
