# Port từ content-extractor/lib/smart-split.ts — đồng bộ 2 nơi khi fix bug.
# Cắt canvas dài (đã stitch) thành band theo ngữ cảnh: gutter trắng dày làm điểm cắt.
# Pipeline: compute_safe_runs → choose_cuts (greedy, gutter dày nhất trong cửa sổ)
# → regions_from_cuts → merge_low_ink_bands.
#
# Grayscale BẮT BUỘC công thức integer (299R+587G+114B)//1000 — KHÔNG dùng
# PIL convert('L') (rounding khác → lệch safe-line biên, vỡ parity).

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SPLIT_DEFAULTS = {
    "line_tolerance": 25,  # chênh lệch pixel liền kề tối đa của hàng "an toàn" (0-255)
    "ignorable_border": 5,  # bỏ qua N px sát mép trái/phải khi xét hàng
    "min_gutter_height": 24,  # run an toàn liên tục ≥ ngưỡng này mới là ứng viên cắt
    "split_height": 1600,  # chiều cao band cơ sở → min/max suy ra 0.6×/1.4×
    "min_ink_density": 0.02,  # band thưa mực hơn → gộp vào band liền kề
}


@dataclass(frozen=True)
class SplitSettings:
    line_tolerance: int = SPLIT_DEFAULTS["line_tolerance"]
    ignorable_border: int = SPLIT_DEFAULTS["ignorable_border"]
    min_gutter_height: int = SPLIT_DEFAULTS["min_gutter_height"]
    min_band_height: int = round(0.6 * SPLIT_DEFAULTS["split_height"])
    max_band_height: int = round(1.4 * SPLIT_DEFAULTS["split_height"])
    min_ink_density: float = SPLIT_DEFAULTS["min_ink_density"]

    @staticmethod
    def from_split_height(split_height: int) -> "SplitSettings":
        # Backward-compat smart-split.ts:44-54: min/max suy từ SPLIT_HEIGHT
        return SplitSettings(
            min_band_height=round(0.6 * split_height),
            max_band_height=round(1.4 * split_height),
        )


def to_gray(rgb: np.ndarray) -> np.ndarray:
    """RGB(A)/gray uint8 (H,W,C) hoặc (H,W) → gray uint8 (H,W), công thức integer repo gốc."""
    if rgb.ndim == 2:
        return rgb.astype(np.uint8)
    channels = rgb.shape[2]
    if channels == 1:
        return rgb[:, :, 0].astype(np.uint8)
    if channels == 2:  # gray + alpha — lấy kênh gray (smart-split.ts:73-77)
        return rgb[:, :, 0].astype(np.uint8)
    r = rgb[:, :, 0].astype(np.int32)
    g = rgb[:, :, 1].astype(np.int32)
    b = rgb[:, :, 2].astype(np.int32)
    gray = (299 * r + 587 * g + 114 * b) // 1000
    return np.minimum(gray, 255).astype(np.uint8)


def compute_safe_lines(gray: np.ndarray, s: SplitSettings) -> np.ndarray:
    """Mask bool (H,) — hàng an toàn khi MỌI cặp pixel kề trong [border, w-border) chênh ≤ tol."""
    height, width = gray.shape
    start_x = s.ignorable_border
    end_x = max(0, width - s.ignorable_border - 1)
    if start_x >= end_x:
        return np.ones(height, dtype=bool)  # ảnh quá hẹp — coi như an toàn (khớp repo gốc)
    seg = gray[:, start_x : end_x + 1].astype(np.int16)
    diffs = np.abs(np.diff(seg, axis=1))
    return (diffs.max(axis=1) <= s.line_tolerance) if diffs.shape[1] > 0 else np.ones(height, dtype=bool)


def compute_safe_runs(gray: np.ndarray, s: SplitSettings) -> list[tuple[int, int]]:
    """Gom hàng an toàn liên tiếp thành run [(start, end_exclusive)] (smart-split.ts:111-124)."""
    safe = compute_safe_lines(gray, s)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for y in range(len(safe)):
        if safe[y]:
            if start is None:
                start = y
        elif start is not None:
            runs.append((start, y))
            start = None
    if start is not None:
        runs.append((start, len(safe)))
    return runs


def _last_safe_row_in_range(runs: list[tuple[int, int]], lo: int, hi: int) -> int | None:
    # smart-split.ts:129-138 — hàng an toàn gần hi nhất trong [lo, hi]
    best: int | None = None
    for r_start, r_end in runs:
        if r_start > hi:
            break
        row_end = r_end - 1
        if row_end < lo:
            continue
        best = min(row_end, hi)
    return best


def choose_cuts(runs: list[tuple[int, int]], height: int, s: SplitSettings) -> list[int]:
    """Greedy chọn điểm cắt y tuyệt đối (smart-split.ts:144-166)."""
    gutters = [
        {"center": (start + end) // 2, "thickness": end - start}
        for start, end in runs
        if end - start >= s.min_gutter_height
    ]
    cuts: list[int] = []
    current_y = 0
    while height - current_y > s.max_band_height:
        lo = current_y + s.min_band_height
        hi = current_y + s.max_band_height
        in_win = [g for g in gutters if lo < g["center"] <= hi]
        if in_win:
            in_win.sort(key=lambda g: (-g["thickness"], g["center"]))
            cut_y = in_win[0]["center"]
        else:
            cut_y = _last_safe_row_in_range(runs, lo, hi)
            if cut_y is None:
                cut_y = hi
        cuts.append(cut_y)
        current_y = cut_y
    return cuts


def regions_from_cuts(cuts: list[int], width: int, height: int) -> list[dict]:
    """Cuts → band full-width liền mạch phủ kín [0, height) (smart-split.ts:170-179)."""
    regions: list[dict] = []
    current_y = 0
    for cut in cuts:
        regions.append({"x": 0, "y": current_y, "w": width, "h": cut - current_y})
        current_y = cut
    regions.append({"x": 0, "y": current_y, "w": width, "h": height - current_y})
    return regions


def region_ink_density(gray: np.ndarray, r: dict) -> float:
    # smart-split.ts:181-190 — tỉ lệ pixel mực (gray < 250) trong region
    patch = gray[r["y"] : r["y"] + r["h"], r["x"] : r["x"] + r["w"]]
    return float(np.count_nonzero(patch < 250)) / (r["w"] * r["h"])


def merge_low_ink_bands(regions: list[dict], gray: np.ndarray, s: SplitSettings) -> list[dict]:
    """Gộp band non nội dung vào band liền kề — một pass (smart-split.ts:206-244)."""
    if len(regions) <= 1:
        return [dict(r) for r in regions]

    densities = [region_ink_density(gray, r) for r in regions]
    merged: list[dict] = []
    carry: dict | None = None  # chuỗi band đầu mật độ thấp, chờ gộp về sau

    def extend_carry(c: dict | None, r: dict) -> dict:
        if c is None:
            return dict(r)
        return {"x": 0, "y": c["y"], "w": c["w"], "h": c["h"] + r["h"]}

    for i, r in enumerate(regions):
        if densities[i] < s.min_ink_density:
            if merged:
                merged[-1] = {**merged[-1], "h": merged[-1]["h"] + r["h"]}
            else:
                carry = extend_carry(carry, r)
        else:
            merged.append(extend_carry(carry, r))
            carry = None
    if carry is not None:
        # Toàn bộ regions mật độ thấp — giữ 1 band
        if merged:
            merged[-1] = {**merged[-1], "h": merged[-1]["h"] + carry["h"]}
        else:
            merged.append(carry)

    if len(merged) > 1 and merged[-1]["h"] < s.min_band_height:
        last = merged.pop()
        merged[-1] = {**merged[-1], "h": merged[-1]["h"] + last["h"]}

    return merged


def split_gray(gray: np.ndarray, s: SplitSettings) -> list[dict]:
    """Canvas gray → band regions [{x,y,w,h}] (smart-split.ts:247-252)."""
    runs = compute_safe_runs(gray, s)
    cuts = choose_cuts(runs, gray.shape[0], s)
    regions = regions_from_cuts(cuts, gray.shape[1], gray.shape[0])
    return merge_low_ink_bands(regions, gray, s)
