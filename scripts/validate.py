#!/usr/bin/env python3
# Port business rules từ content-extractor/lib/script.ts:164-228 (toScriptSegments —
# index ngoài biên / lặp / bỏ sót) — đồng bộ 2 nơi khi fix bug. Khác TS: TS loại-và-cảnh-báo
# để người duyệt sửa qua UI; validate.py FAIL CỨNG để orchestrator (agent) tự sửa rồi chạy lại.
#
# CLI: validate.py --workdir W --stage content|script
#      validate.py --series <series>/.mtv     (series-memory — xem references/series.md)
# Lỗi in ra JSON lines {"file","path","rule","message"} → exit 1; pass → summary 1 dòng, exit 0.
# Cap độ dài bible/summary chỉ WARNING (stderr) — vượt cap không chặn pipeline.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jsonschema import Draft202012Validator

from mtv.schema_defs import (
    content_dir,
    content_path,
    estimate_seconds,
    load_json,
    load_manifest,
    script_path,
)

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

_errors_found = False


def emit_error(file: str, path: str, rule: str, message: str) -> None:
    global _errors_found
    _errors_found = True
    print(json.dumps({"file": file, "path": path, "rule": rule, "message": message}, ensure_ascii=False))


def emit_warning(file: str, path: str, rule: str, message: str) -> None:
    # Cảnh báo không fail — in ra stderr để không lẫn vào stream lỗi máy-đọc
    print(
        json.dumps(
            {"file": file, "path": path, "rule": rule, "message": message, "level": "warning"},
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )


def json_pointer(base: str, error_path) -> str:
    # jsonschema absolute_path (deque) → dạng $.a[0].b cho người + máy đọc
    out = base
    for part in error_path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def check_schema(instance, schema_file: str, file_label: str) -> bool:
    validator = Draft202012Validator(load_json(SCHEMAS_DIR / schema_file))
    ok = True
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        emit_error(file_label, json_pointer("$", err.absolute_path), "schema", err.message)
        ok = False
    return ok


def validate_content(workdir: Path) -> None:
    manifest = load_manifest(workdir)
    slices = manifest.get("slices", [])
    for s in slices:
        sid = s["id"]
        cpath = content_path(workdir, sid)
        rel = f"content/{sid}.json"
        if s.get("status") == "described":
            if not cpath.exists():
                emit_error(rel, "$", "content-missing", f"slice {sid} status=described nhưng thiếu file content")
                continue
            try:
                data = load_json(cpath)
            except json.JSONDecodeError as e:
                emit_error(rel, "$", "json-parse", str(e))
                continue
            if check_schema(data, "content.schema.json", rel) and data["slice_id"] != sid:
                emit_error(rel, "$.slice_id", "slice-id-mismatch", f"slice_id '{data['slice_id']}' ≠ tên file '{sid}'")
        elif cpath.exists():
            emit_warning(rel, "$", "status-drift", f"slice {sid} có file content nhưng manifest còn '{s.get('status')}' — cập nhật manifest")

    described = sum(1 for s in slices if s.get("status") == "described")
    if not _errors_found:
        print(f"OK content: {described}/{len(slices)} slice described, tất cả pass schema")


def validate_script(workdir: Path) -> None:
    manifest = load_manifest(workdir)
    n_slices = len(manifest.get("slices", []))
    spath = script_path(workdir)
    rel = "script.json"
    if not spath.exists():
        emit_error(rel, "$", "script-missing", "chưa có script.json trong workdir")
        return
    try:
        data = load_json(spath)
    except json.JSONDecodeError as e:
        emit_error(rel, "$", "json-parse", str(e))
        return
    if not check_schema(data, "script.schema.json", rel):
        return

    segments = data["segments"]

    # order phải liên tục từ 0 (port quy ước order = segments.length lúc build, script.ts:205)
    for i, seg in enumerate(segments):
        if seg["order"] != i:
            emit_error(rel, f"$.segments[{i}].order", "order-not-contiguous", f"order={seg['order']}, kỳ vọng {i} (liên tục từ 0)")

    # narration không rỗng sau trim (schema chỉ bắt minLength=1 thô)
    for i, seg in enumerate(segments):
        if not seg["narration"].strip():
            emit_error(rel, f"$.segments[{i}].narration", "narration-empty", "narration rỗng sau trim")

    # Phủ index: mỗi shot_index ∈ [0, n_slices), xuất hiện ĐÚNG 1 lần toàn cục
    # (port script.ts:188-201 out-of-range/duplicate + :221-226 bỏ sót)
    seen: dict[int, str] = {}
    for i, seg in enumerate(segments):
        for j, idx in enumerate(seg["shot_indices"]):
            ptr = f"$.segments[{i}].shot_indices[{j}]"
            if idx >= n_slices:
                emit_error(rel, ptr, "index-out-of-range", f"index {idx} ngoài biên [0,{n_slices - 1}]")
            elif idx in seen:
                emit_error(rel, ptr, "index-duplicate", f"index {idx} đã xuất hiện ở {seen[idx]}")
            else:
                seen[idx] = ptr
    missed = [i for i in range(n_slices) if i not in seen]
    if missed:
        emit_error(rel, "$.segments", "index-missing", f"bỏ sót {len(missed)} slice: {missed}")

    if not _errors_found:
        total = sum(estimate_seconds(seg["narration"]) for seg in segments)
        print(f"OK script: {len(segments)} segment phủ đủ {n_slices} slice, ước tính ~{total}s audio")


# Cap từ theo references/series.md — đếm bằng str.split() (đủ cho warning, không cần tách từ tiếng Việt chuẩn)
BIBLE_WORD_CAP = 2000
SUMMARY_WORD_CAP = 250


def word_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").split())


def validate_series(mtv_dir: Path) -> None:
    spath = mtv_dir / "series.json"
    rel = "series.json"
    if not spath.exists():
        emit_error(rel, "$", "series-missing", f"không tìm thấy series.json trong {mtv_dir}")
        return
    try:
        data = load_json(spath)
    except json.JSONDecodeError as e:
        emit_error(rel, "$", "json-parse", str(e))
        return
    if not check_schema(data, "series.schema.json", rel):
        return

    chapters = data["chapters"]
    slugs = {ch["slug"] for ch in chapters}

    for i, ch in enumerate(chapters):
        slug = ch["slug"]
        summary = mtv_dir / "summaries" / f"{slug}.md"
        if not summary.exists() or not summary.read_text(encoding="utf-8").strip():
            emit_error(f"summaries/{slug}.md", "$", "summary-missing", f"chương '{slug}' status=done nhưng thiếu/rỗng recap")
        elif (n := word_count(summary)) > SUMMARY_WORD_CAP:
            emit_warning(f"summaries/{slug}.md", "$", "summary-over-cap", f"recap {n} từ > cap {SUMMARY_WORD_CAP} — gọt lại")
        for ref in ch["context_recaps_used"]:
            if ref not in slugs:
                emit_error(rel, f"$.chapters[{i}].context_recaps_used", "recap-ref-unknown", f"'{ref}' không có trong chapters")

    bible = mtv_dir / "bible.md"
    if chapters and (not bible.exists() or not bible.read_text(encoding="utf-8").strip()):
        emit_error("bible.md", "$", "bible-missing", "đã có chương done nhưng thiếu/rỗng bible.md")
    elif bible.exists() and (n := word_count(bible)) > BIBLE_WORD_CAP:
        emit_warning("bible.md", "$", "bible-over-cap", f"bible {n} từ > cap {BIBLE_WORD_CAP} — có thể đã lẫn diễn biến, gọt lại")

    if not _errors_found:
        print(f"OK series: {len(chapters)} chương done, index + summaries + bible khớp")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate hợp đồng workdir manga-to-video")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--stage", choices=["content", "script"])
    parser.add_argument("--series", type=Path, metavar="MTV_DIR", help="validate series-memory (<series>/.mtv)")
    args = parser.parse_args()

    if args.series is not None:
        if args.workdir or args.stage:
            parser.error("--series không dùng chung với --workdir/--stage")
        validate_series(args.series)
        sys.exit(1 if _errors_found else 0)

    if not args.workdir or not args.stage:
        parser.error("cần --workdir + --stage (hoặc --series)")

    if not (args.workdir / "manifest.json").exists():
        emit_error("manifest.json", "$", "manifest-missing", f"không tìm thấy manifest.json trong {args.workdir}")
        sys.exit(1)

    if args.stage == "content":
        validate_content(args.workdir)
    else:
        validate_script(args.workdir)
    sys.exit(1 if _errors_found else 0)


if __name__ == "__main__":
    main()
