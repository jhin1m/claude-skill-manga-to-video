#!/usr/bin/env python3
# Sinh fixtures cho smoke test validate.py — chạy lại được (idempotent).
# Mỗi fixture là 1 workdir tối giản 3 slice; case fail chỉ chứa ĐÚNG 1 lỗi để
# smoke test khẳng định rule phân biệt được.

from __future__ import annotations

import json
import shutil
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def manifest(n: int = 3, status: str = "described") -> dict:
    return {
        "version": 1,
        "created_at": "2026-07-19T00:00:00Z",
        "source": {"type": "folder", "path": "/fake", "file_count": n},
        "settings": {"split_height": 1600},
        "stitched": {"width": 720, "height": 4000},
        "slices": [
            {"id": f"{i:03d}", "file": f"slices/{i:03d}.png", "w": 720, "h": 1300, "y_src": i * 1300, "status": status}
            for i in range(n)
        ],
        "stages": {"ingest": "done", "content": "partial", "script": "pending", "tts": "pending", "render": "pending"},
    }


def content(sid: str, description: str = "Nhân vật đứng trước cổng.", dialogue=None) -> dict:
    return {"slice_id": sid, "description": description, "dialogue": dialogue or [], "note": ""}


def script(segments) -> dict:
    return {"version": 1, "segments": segments}


def seg(i: int, indices, narration: str = "Lời kể ngôi ba đủ dài.") -> dict:
    return {"id": f"seg-{i:03d}", "order": i, "narration": narration, "shot_indices": indices}


def write_workdir(name: str, mani: dict, contents: dict[str, dict] | None = None, scr: dict | None = None) -> None:
    d = FIXTURES / name
    if d.exists():
        shutil.rmtree(d)
    (d / "content").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(mani, ensure_ascii=False, indent=2), encoding="utf-8")
    for sid, c in (contents or {}).items():
        (d / "content" / f"{sid}.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    if scr is not None:
        (d / "script.json").write_text(json.dumps(scr, ensure_ascii=False, indent=2), encoding="utf-8")


def full_contents() -> dict[str, dict]:
    return {f"{i:03d}": content(f"{i:03d}") for i in range(3)}


# ---- series-memory fixtures (mô phỏng <series>/.mtv — xem references/series.md) ----

BIBLE = "# Bible: Bộ Test\n\n## Nhân vật\n- **Vân Chi** — nữ chính. Xưng hô: ta. Quan hệ: vợ Mặc Trạch.\n"
RECAP = "# Recap {slug}: Bộ Test\n\nVân Chi gả vào phủ, ra mắt ba đứa trẻ.\n\nKết chương: đêm tân hôn còn treo.\n"


def series_entry(slug: str, recaps_used: list[str] | None = None) -> dict:
    return {"slug": slug, "status": "done", "completed_at": "2026-07-19T00:00:00Z", "context_recaps_used": recaps_used or []}


def write_series(name: str, entries: list[dict], bible: str | None = BIBLE, summaries: list[str] | None = None) -> None:
    # summaries=None → tự sinh đủ recap cho mọi entry; truyền list slug để chủ động làm thiếu
    d = FIXTURES / name
    if d.exists():
        shutil.rmtree(d)
    (d / "summaries").mkdir(parents=True)
    (d / "series.json").write_text(
        json.dumps({"version": 1, "series_title": "Bộ Test", "chapters": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if bible is not None:
        (d / "bible.md").write_text(bible, encoding="utf-8")
    for slug in [e["slug"] for e in entries] if summaries is None else summaries:
        (d / "summaries" / f"{slug}.md").write_text(RECAP.format(slug=slug), encoding="utf-8")


def main() -> None:
    # ---- content stage ----
    write_workdir("content-pass", manifest(), full_contents())
    c = full_contents()
    del c["002"]
    write_workdir("content-fail-missing-file", manifest(), c)
    c = full_contents()
    c["001"]["description"] = ""
    write_workdir("content-fail-empty-description", manifest(), c)
    c = full_contents()
    c["001"]["dialogue"] = "không phải array"
    write_workdir("content-fail-bad-type", manifest(), c)
    # drift: manifest pending nhưng content đã có → warning, exit 0
    write_workdir("content-warn-status-drift", manifest(status="pending"), full_contents())

    # ---- script stage ----
    write_workdir("script-pass", manifest(), full_contents(), script([seg(0, [0, 1]), seg(1, [2])]))
    write_workdir("script-fail-missing-index", manifest(), full_contents(), script([seg(0, [0, 1])]))
    write_workdir("script-fail-duplicate-index", manifest(), full_contents(), script([seg(0, [0, 1]), seg(1, [1, 2])]))
    write_workdir("script-fail-empty-narration", manifest(), full_contents(), script([seg(0, [0, 1]), seg(1, [2], narration="   ")]))
    write_workdir("script-fail-bad-type", manifest(), full_contents(), script([seg(0, [0, "1"]), seg(1, [2])]))
    write_workdir("script-fail-out-of-range", manifest(), full_contents(), script([seg(0, [0, 1]), seg(1, [2, 5])]))

    # ---- series stage ----
    two = [series_entry("chapter-1.1"), series_entry("chapter-1.2", ["chapter-1.1"])]
    write_series("series-pass", two)
    write_series("series-fail-missing-summary", two, summaries=["chapter-1.1"])
    bad = [dict(series_entry("chapter-1.1")), series_entry("chapter-1.2", ["chapter-1.1"])]
    del bad[0]["completed_at"]
    write_series("series-fail-bad-schema", bad)
    write_series("series-fail-recap-ref", [series_entry("chapter-1.1"), series_entry("chapter-1.2", ["chapter-99"])])
    write_series("series-warn-bible-over-cap", two, bible="# Bible: Bộ Test\n\n" + "từ " * 2100)

    print(f"Fixtures ghi vào {FIXTURES}")


if __name__ == "__main__":
    main()
