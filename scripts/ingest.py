#!/usr/bin/env python3
# Port từ content-extractor/lib/pipeline.ts (ingestBandItems) + jobs/route.ts —
# đồng bộ 2 nơi khi fix bug. Điểm KHÁC TS (skill-level): sau split có bước trim lề
# từng band bằng ink_bbox (TS để nút "Vừa khít nội dung" cho người dùng bấm tay).
#
# CLI: ingest.py --input <folder|pdf> --workdir W [--split-height 1600] [--pdf-scale 2]
#                [--max-edge 1568] [--no-trim] [--resume]
# Output: workdir/slices/NNN.png + manifest.json (settings snapshot, slices status pending).

from __future__ import annotations

import argparse
import datetime
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

from mtv.ingest_core import (
    MAX_EDGE_DEFAULT,
    STRIP_RATIO_DEFAULT,
    ingest_canvas,
    list_folder_images,
)
from mtv.ink_bbox import ink_bbox
from mtv.pdf_pages import PDF_SCALE_DEFAULT, render_pdf
from mtv.smart_split import SplitSettings
from mtv.schema_defs import (
    MANIFEST_VERSION,
    load_manifest,
    manifest_path,
    save_manifest,
    slices_dir,
)


def fail(message: str) -> None:
    print(json.dumps({"file": "ingest", "path": "$", "rule": "ingest-error", "message": message}, ensure_ascii=False))
    sys.exit(1)


def load_input(input_path: Path, pdf_scale: float) -> tuple[str, list[bytes], int]:
    """Trả (source_type, list ảnh bytes theo thứ tự đọc, file_count)."""
    if input_path.is_dir():
        files = list_folder_images(input_path)
        if not files:
            fail(f"Không tìm thấy ảnh nào trong {input_path}")
        return "folder", [p.read_bytes() for p in files], len(files)
    if input_path.suffix.lower() == ".pdf":
        pages = render_pdf(input_path.read_bytes(), scale=pdf_scale)
        return "pdf", pages, len(pages)
    fail(f"Input phải là folder ảnh hoặc file .pdf: {input_path}")
    raise AssertionError  # unreachable


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest ảnh manga → slices + manifest (band-split + trim)")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--split-height", type=int, default=1600)
    parser.add_argument("--pdf-scale", type=float, default=PDF_SCALE_DEFAULT)
    parser.add_argument("--max-edge", type=int, default=MAX_EDGE_DEFAULT)
    parser.add_argument("--no-trim", action="store_true", help="giữ nguyên band, bỏ bước trim lề ink-bbox")
    parser.add_argument("--resume", action="store_true", help="workdir có manifest → chỉ in trạng thái, không làm lại")
    args = parser.parse_args()

    if manifest_path(args.workdir).exists():
        if args.resume:
            manifest = load_manifest(args.workdir)
            described = sum(1 for s in manifest["slices"] if s["status"] == "described")
            print(f"RESUME: workdir đã ingest {len(manifest['slices'])} slice "
                  f"({described} described) | stages: {json.dumps(manifest['stages'])}")
            return
        fail(f"{args.workdir} đã có manifest.json — dùng --resume để xem trạng thái hoặc chọn workdir khác")

    if not args.input.exists():
        fail(f"Input không tồn tại: {args.input}")

    source_type, raw_images, file_count = load_input(args.input, args.pdf_scale)
    settings = SplitSettings.from_split_height(args.split_height)
    canvas, _, regions = ingest_canvas(raw_images, max_edge=args.max_edge, settings=settings)

    sdir = slices_dir(args.workdir)
    sdir.mkdir(parents=True, exist_ok=True)
    slices_meta = []
    for i, band in enumerate(regions):
        sid = f"{i:03d}"
        crop = canvas.crop((band["x"], band["y"], band["x"] + band["w"], band["y"] + band["h"]))
        trim = None
        if not args.no_trim:
            trim = ink_bbox(np.asarray(crop))
            if trim is not None:
                crop = crop.crop((trim["x"], trim["y"], trim["x"] + trim["w"], trim["y"] + trim["h"]))
        crop.save(sdir / f"{sid}.png", format="PNG")
        slices_meta.append({
            "id": sid,
            "file": f"slices/{sid}.png",
            "w": crop.width,
            "h": crop.height,
            "y_src": band["y"],
            "trim": trim,  # None = band giữ nguyên (toàn trắng / sliver / full-bleed)
            "status": "pending",
        })

    manifest = {
        "version": MANIFEST_VERSION,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": {"type": source_type, "path": str(args.input.resolve()), "file_count": file_count},
        "settings": {
            "split_height": args.split_height,
            "line_tolerance": settings.line_tolerance,
            "ignorable_border": settings.ignorable_border,
            "min_gutter_height": settings.min_gutter_height,
            "min_band_height": settings.min_band_height,
            "max_band_height": settings.max_band_height,
            "min_ink_density": settings.min_ink_density,
            "max_edge": args.max_edge,
            "strip_ratio": STRIP_RATIO_DEFAULT,
            "pdf_scale": args.pdf_scale,
            "trim": not args.no_trim,
        },
        "stitched": {"width": canvas.width, "height": canvas.height},
        "slices": slices_meta,
        "stages": {"ingest": "done", "content": "pending", "script": "pending", "tts": "pending", "render": "pending"},
    }
    save_manifest(args.workdir, manifest)

    trimmed = sum(1 for s in slices_meta if s["trim"] is not None)
    print(f"OK ingest: {file_count} ảnh nguồn → canvas {canvas.width}×{canvas.height} "
          f"→ {len(slices_meta)} slice ({trimmed} trimmed) → {sdir}")


if __name__ == "__main__":
    main()
