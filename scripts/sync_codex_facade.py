#!/usr/bin/env python3
"""Đồng bộ SKILL.md sang facade Codex (`~/.codex/skills/manga-to-video/`).

Trước đây facade là bản chép TAY giữ 5 khác biệt so với bản gốc, và quy trình "chép lại rồi
nhớ áp 5 khác biệt" đã hỏng: facade trễ 2 feature (auto-region v2.1, style phụ đề) suốt hơn
một tuần. Hỏng IM LẶNG — engine dùng chung nên code mới vẫn chạy, chỉ là Codex không biết các
flag mới tồn tại. Nay hai file giống nhau byte-for-byte, script này là đường sync duy nhất.

Vì sao là file thật, không phải symlink/hardlink:
  - Codex KHÔNG đi theo symlink khi khám phá skill (đã probe).
  - Hardlink thì editor ghi kiểu write-new-then-rename sẽ phá liên kết âm thầm — đúng cái
    bug đang chữa.

Chạy: sync_codex_facade.py            # chép, tạo thư mục nếu chưa có
      sync_codex_facade.py --check    # chỉ so sha256, lệch → exit 1, không ghi
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "SKILL.md"
# Codex CLI đọc CODEX_HOME (mặc định ~/.codex). Hardcode ~/.codex thì trên máy có đặt biến đó
# sẽ ghi vào thư mục chết và --check báo xanh trong khi Codex vẫn đọc bản cũ — đúng bug đang chữa.
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
FACADE = CODEX_HOME / "skills" / "manga-to-video" / "SKILL.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Đồng bộ SKILL.md sang facade Codex")
    parser.add_argument("--check", action="store_true", help="chỉ so sha256, không ghi")
    args = parser.parse_args()

    if not SOURCE.exists():
        print(f"[sync] KHÔNG thấy nguồn: {SOURCE}", file=sys.stderr)
        sys.exit(1)
    src_hash = sha256(SOURCE)

    if args.check:
        if not FACADE.exists():
            print(f"[sync] ✗ facade chưa tồn tại: {FACADE}\n       chạy sync_codex_facade.py để tạo", file=sys.stderr)
            sys.exit(1)
        dst_hash = sha256(FACADE)
        if src_hash != dst_hash:
            print(
                f"[sync] ✗ facade lệch bản gốc\n"
                f"       {SOURCE}\n         sha256 {src_hash}\n"
                f"       {FACADE}\n         sha256 {dst_hash}\n"
                f"       chạy sync_codex_facade.py để chép đè (bản gốc là nguồn chân lý)",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[sync] ✓ facade khớp bản gốc (sha256 {src_hash[:12]}…)")
        return

    FACADE.parent.mkdir(parents=True, exist_ok=True)
    unchanged = FACADE.exists() and sha256(FACADE) == src_hash
    shutil.copyfile(SOURCE, FACADE)
    print(f"[sync] {'✓ đã khớp sẵn' if unchanged else '✓ đã chép'} → {FACADE} (sha256 {src_hash[:12]}…)")


if __name__ == "__main__":
    main()
