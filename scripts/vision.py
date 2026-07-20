#!/usr/bin/env python3
# Stage vision: đọc slice bằng z.ai GLM thay cho subagent (tiết kiệm token model đắt cho
# việc thuần cơ học). Port từ content-extractor/lib/vision.ts — đồng bộ 2 nơi khi fix bug.
#
# CLI: vision.py --workdir W [--model glm-4.5v] [--base-url URL] [--context-file F]
#                [--concurrency 3] [--force] [--only 001,002]
# Đọc manifest.json → gọi z.ai cho slice chưa có content/NNN.json → ghi content/NNN.json
# → cập nhật slices[].status + stages.content. Script này là single-writer của manifest
# trong stage vision (chạy inline, không song song với batch subagent).
#
# Resume miễn phí: slice đã có content/NNN.json thì bỏ qua (dùng --force để đọc lại).

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtv import vision_zai
from mtv.schema_defs import (
    content_dir,
    content_path,
    load_manifest,
    save_json_atomic,
    save_manifest,
    slices_dir,
)

DEFAULT_CONCURRENCY = 3  # Coding Plan giới hạn tần suất theo phút khá chặt (lỗi 1302)


def fail(rule: str, message: str) -> None:
    print(json.dumps({"file": "vision", "path": "$", "rule": rule, "message": message}, ensure_ascii=False))
    sys.exit(1)


def select_slices(manifest: dict, workdir: Path, only: str, force: bool) -> list[str]:
    """Slice cần đọc: mặc định là slice chưa có file content; --only lọc thêm theo id."""
    wanted = {s.strip() for s in only.split(",") if s.strip()} if only else None
    if wanted:
        known = {s["id"] for s in manifest["slices"]}
        unknown = sorted(wanted - known)
        if unknown:
            fail("slice-unknown", f"--only chứa slice không có trong manifest: {', '.join(unknown)}")
    todo = []
    for s in manifest["slices"]:
        sid = s["id"]
        if wanted is not None and sid not in wanted:
            continue
        if force or not content_path(workdir, sid).exists():
            todo.append(sid)
    return todo


def sync_manifest(manifest: dict, workdir: Path) -> int:
    """Đồng bộ manifest theo file content THỰC CÓ trên đĩa (nguồn chân lý là filesystem)."""
    described = 0
    for s in manifest["slices"]:
        if content_path(workdir, s["id"]).exists():
            s["status"] = "described"
            described += 1
    total = len(manifest["slices"])
    if described == 0:
        manifest["stages"]["content"] = "pending"
    else:
        manifest["stages"]["content"] = "done" if described == total else "partial"
    return described


def main() -> None:
    parser = argparse.ArgumentParser(description="Đọc slice bằng z.ai GLM → content/NNN.json")
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--context-file", type=Path,
                        help="file ngữ cảnh chương / khối context series (tuỳ chọn)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--force", action="store_true", help="đọc lại cả slice đã có content")
    parser.add_argument("--only", default="", help="chỉ đọc các slice này, vd 003,004")
    args = parser.parse_args()

    api_key = vision_zai.resolve_api_key()
    if not api_key:
        fail("api-key-missing",
             f"Thiếu {vision_zai.API_KEY_NAME} (env hoặc .secrets.json). Không có key thì dùng "
             "subagent manga-vision-reader theo SKILL.md bước 2.")

    model = args.model or os.environ.get("VISION_MODEL", "").strip() or vision_zai.DEFAULT_MODEL
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "").strip() or vision_zai.DEFAULT_BASE_URL

    context = ""
    if args.context_file:
        try:
            context = args.context_file.read_text(encoding="utf-8")
        except OSError as err:
            fail("context-unreadable", f"không đọc được --context-file: {err}")
    prompt = vision_zai.build_prompt(context)

    try:
        manifest = load_manifest(args.workdir)
    except (OSError, json.JSONDecodeError) as err:
        fail("manifest-unreadable", f"không đọc được manifest.json: {err} (chạy ingest.py trước?)")

    todo = select_slices(manifest, args.workdir, args.only, args.force)
    total = len(manifest["slices"])
    content_dir(args.workdir).mkdir(parents=True, exist_ok=True)
    print(f"[vision] {len(todo)}/{total} slice cần đọc | model {model} | concurrency {args.concurrency}")

    failures: list[tuple[str, str]] = []

    def work(sid: str) -> None:
        png = slices_dir(args.workdir) / f"{sid}.png"
        try:
            scene = vision_zai.describe(png.read_bytes(), prompt, model, base_url, api_key)
            save_json_atomic(content_path(args.workdir, sid), {"slice_id": sid, **scene})
            print(f"  {sid}: OK ({len(scene['dialogue'])} thoại)")
        except Exception as err:  # noqa: BLE001 — lỗi 1 slice không được giết cả batch
            failures.append((sid, str(err)))
            print(f"  {sid}: FAIL {err}", file=sys.stderr)

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            list(pool.map(work, todo))

    described = sync_manifest(manifest, args.workdir)
    save_manifest(args.workdir, manifest)

    print(f"OK vision: {described}/{total} slice đã đọc | stages.content = "
          f"{manifest['stages']['content']}")
    if failures:
        for sid, msg in failures:
            print(json.dumps({"file": "vision", "path": f"slices/{sid}.png",
                              "rule": "describe-failed", "message": msg}, ensure_ascii=False))
        print(f"[vision] {len(failures)} slice lỗi — chạy lại lệnh (slice đã xong được bỏ qua) "
              f"hoặc --only {','.join(sid for sid, _ in failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
