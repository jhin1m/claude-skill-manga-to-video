# Port từ content-extractor/lib/pipeline.ts (extractImages :57-68, downscale :74-87,
# ingestBandItems :102-133) — đồng bộ 2 nơi khi fix bug.
# Production LUÔN stitch tất cả ảnh thành MỘT canvas dọc rồi split (jobs/route.ts:60
# gọi ingestBandItems vô điều kiện) — Python giữ đúng hành vi đó.
#
# QUAN TRỌNG parity: downscale luôn round-trip JPEG quality 80 (kể cả khi không resize)
# vì sharp .jpeg({quality:80}).toBuffer() luôn re-encode — pixel canvas phụ thuộc bước này.

from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .smart_split import SplitSettings, split_gray, to_gray
from .stitch import stitch_slices

IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif)$", re.IGNORECASE)
MAX_EDGE_DEFAULT = 1568  # vision.ts:26
STRIP_RATIO_DEFAULT = 2.5  # source-kind.ts:7
JPEG_QUALITY = 80

# PIL mặc định chặn ảnh > ~178MP chống decompression bomb — canvas manga dài vượt xa mức đó
Image.MAX_IMAGE_PIXELS = None


def natural_sort_key(name: str):
    """Khớp localeCompare(numeric, sensitivity 'base') pipeline.ts:39-41: 1,2,…,9,10,11."""
    return [
        (0, int(tok)) if tok.isdigit() else (1, tok.casefold())
        for tok in re.split(r"(\d+)", name)
        if tok != ""
    ]


def is_usable_image_name(filename: str) -> bool:
    # pipeline.ts:43-46 — loại file ẩn/metadata (.DS_Store, AppleDouble ._x.jpg)
    return bool(IMAGE_EXT_RE.search(filename)) and not filename.startswith(".")


def list_folder_images(folder: Path) -> list[Path]:
    """Ảnh trong folder theo natural sort — thứ tự đọc là yêu cầu cứng."""
    files = [p for p in Path(folder).iterdir() if p.is_file() and is_usable_image_name(p.name)]
    return sorted(files, key=lambda p: natural_sort_key(p.name))


def downscale_image(
    data: bytes,
    max_edge: int = MAX_EDGE_DEFAULT,
    strip_ratio: float = STRIP_RATIO_DEFAULT,
) -> bytes:
    """Port downscale (pipeline.ts:74-87): ảnh dải dọc chỉ kẹp BỀ RỘNG; luôn re-encode JPEG q80."""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)  # khớp sharp .rotate() — tôn trọng EXIF orientation
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    w, h = im.size
    is_tall = w > 0 and h / w >= strip_ratio
    if is_tall:
        if w > max_edge:  # withoutEnlargement — chỉ thu nhỏ
            im = im.resize((max_edge, round(h * max_edge / w)), Image.LANCZOS)
    elif w > max_edge or h > max_edge:  # fit-inside max_edge×max_edge, chỉ thu nhỏ
        scale = min(max_edge / w, max_edge / h)
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def ingest_canvas(
    raw_images: list[bytes],
    max_edge: int = MAX_EDGE_DEFAULT,
    strip_ratio: float = STRIP_RATIO_DEFAULT,
    settings: SplitSettings | None = None,
) -> tuple[Image.Image, np.ndarray, list[dict]]:
    """Downscale từng ảnh → stitch → smart-split. Trả (canvas RGB, gray, band regions)."""
    settings = settings or SplitSettings()
    downscaled = [Image.open(io.BytesIO(downscale_image(b, max_edge, strip_ratio))) for b in raw_images]
    canvas = stitch_slices(downscaled)
    gray = to_gray(np.asarray(canvas))
    regions = split_gray(gray, settings)
    return canvas, gray, regions
