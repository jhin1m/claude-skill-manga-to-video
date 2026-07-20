# Port từ content-extractor/lib/ink-bbox.ts — đồng bộ 2 nơi khi fix bug.
# Bbox vùng "có mực": projection profile hàng/cột với ngưỡng TỈ LỆ (không phải
# "gặp pixel mực đầu tiên" — tránh lỗi ImageMagick -trim dừng vì 1 pixel nhiễu JPEG).
# 3 case trả None: toàn trắng / sliver (không cột nào đạt ngưỡng) / full-bleed.

from __future__ import annotations

import math

import numpy as np

from .js_math import js_round
from .smart_split import to_gray

INK_BBOX_DEFAULTS = {
    "ink_max": 250,  # gray < ink_max = pixel mực
    "row_pct": 0.01,  # tỉ lệ pixel mực tối thiểu để hàng/cột tính là "có nội dung"
    "pad_pct": 0.04,  # padding theo % cạnh bbox
    "pad_min_px": 14,  # sàn padding tuyệt đối
}


def ink_bbox(
    rgb: np.ndarray,
    ink_max: int = INK_BBOX_DEFAULTS["ink_max"],
    row_pct: float = INK_BBOX_DEFAULTS["row_pct"],
    pad_pct: float = INK_BBOX_DEFAULTS["pad_pct"],
    pad_min_px: int = INK_BBOX_DEFAULTS["pad_min_px"],
) -> dict | None:
    """Ảnh RGB(A) uint8 → {x,y,w,h} vùng mực + padding, hoặc None (giữ nguyên ảnh)."""
    gray = to_gray(rgb)
    height, width = gray.shape
    if width < 1 or height < 1:
        return None

    mask = gray < ink_max
    row_ink = mask.sum(axis=1)  # pixel mực theo hàng
    col_ink = mask.sum(axis=0)  # pixel mực theo cột

    row_min = max(1, math.ceil(width * row_pct))
    col_min = max(1, math.ceil(height * row_pct))

    top = 0
    while top < height and row_ink[top] < row_min:
        top += 1
    if top == height:
        return None  # ảnh trắng hết — không có gì để trim
    bottom = height - 1
    while bottom > top and row_ink[bottom] < row_min:
        bottom -= 1
    left = 0
    while left < width and col_ink[left] < col_min:
        left += 1
    # Có hàng đủ mực nhưng không cột nào đạt ngưỡng (vd vạch ngang mảnh trên ảnh cao)
    if left >= width:
        return None
    right = width - 1
    while right > left and col_ink[right] < col_min:
        right -= 1

    # Full-bleed (bbox chạm cả 4 cạnh) → không có lề, caller giữ nguyên region
    if top == 0 and left == 0 and bottom == height - 1 and right == width - 1:
        return None

    bw = right - left + 1
    bh = bottom - top + 1
    pad_x = max(pad_min_px, js_round(bw * pad_pct))
    pad_y = max(pad_min_px, js_round(bh * pad_pct))
    x = max(0, left - pad_x)
    y = max(0, top - pad_y)
    return {
        "x": x,
        "y": y,
        "w": min(width, right + pad_x + 1) - x,
        "h": min(height, bottom + pad_y + 1) - y,
    }
