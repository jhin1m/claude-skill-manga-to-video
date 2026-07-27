#!/usr/bin/env python3
"""Chạy mọi cổng kiểm của skill trong một lệnh.

Lý do tồn tại: skill không có CI. Sợi dây parity chỉ siết nếu có người kéo, mà "nhớ chạy
test_parity.py" đúng là loại quy trình vừa hỏng một lần (facade Codex trễ 2 feature suốt hơn
một tuần vì "nhớ áp 5 khác biệt"). Một lệnh duy nhất thì khó quên hơn ba lệnh.

Chạy: ~/.claude/skills/.venv/bin/python3 tests/run_all.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STEPS = [
    ("smoke validate (16 case)", [ROOT / "tests" / "run_smoke.py"]),
    ("auto-region v2.1", [ROOT / "tests" / "test_auto_region.py"]),
    ("parity với snapshot TS", [ROOT / "tests" / "test_parity.py"]),
    # Không phải test hành vi, nhưng cùng loại rủi ro: hai file lệch nhau trong im lặng.
    ("facade Codex khớp SKILL.md", [ROOT / "scripts" / "sync_codex_facade.py", "--check"]),
]


def main() -> None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    failed: list[str] = []
    for label, argv in STEPS:
        print(f"\n=== {label} ===")
        code = subprocess.run([sys.executable, *map(str, argv)], env=env).returncode
        if code != 0:
            failed.append(label)
    print()
    if failed:
        print(f"RUN_ALL FAIL: {', '.join(failed)}")
        sys.exit(1)
    print(f"RUN_ALL PASS: {len(STEPS)}/{len(STEPS)}")


if __name__ == "__main__":
    main()
