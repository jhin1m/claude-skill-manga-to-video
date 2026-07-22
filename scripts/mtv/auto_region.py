# Port từ content-extractor/lib/auto-region.ts — đồng bộ 2 nơi khi fix bug.
#
# Auto-crop "né bubble" v2.1 — calibrate screw-the-noble-life ch1:
#   v1 sliding-window 2D: cắt ngang 40–55% → hỏng composition.
#   v2 edge-peel: art trong bubble chặn trim; bubble không luôn dính mép 0.
#
# v2.1: CHỈ crop dọc (giữ full width) trên band manhwa cao; chọn cửa sổ
# chiều cao ≥ min_keep_h có bubble-mean thấp nhất (integral 1D). Ngang chỉ khi
# ảnh RỘNG (w > h*1.15) — hiếm với webtoon band.

from __future__ import annotations

from typing import Any

import numpy as np

from .js_math import js_round
from .smart_split import to_gray

AUTO_REGION_DEFAULTS = {
    "max_edge": 480,
    "bright_thr": 244,
    "min_bubble_area_frac": 0.003,
    "max_bubble_area_frac": 0.20,
    "min_bubble_side": 10,
    "min_fill": 0.38,
    "min_dark_in_bubble": 0.015,
    "bg_width_frac": 0.85,
    "bg_height_frac": 0.28,
    # giữ tối thiểu sau crop
    "min_keep_h_frac": 0.78,  # dọc: không cắt quá 22% chiều cao
    "min_keep_w_frac": 0.92,  # ngang (chỉ ảnh rộng)
    "min_trim_frac": 0.05,  # phải cắt ≥5% diện tích mới nhận
    "min_bubble_reduce": 0.18,
    "pad_pct": 0.02,
    "pad_min_px": 8,
}


def _clamp_region(r: dict, width: int, height: int) -> dict:
    x = max(0, min(int(js_round(r["x"])), width - 1))
    y = max(0, min(int(js_round(r["y"])), height - 1))
    w = max(1, min(int(js_round(r["w"])), width - x))
    h = max(1, min(int(js_round(r["h"])), height - y))
    return {"x": x, "y": y, "w": w, "h": h}


def _downscale_gray(gray: np.ndarray, max_edge: int) -> tuple[np.ndarray, float]:
    h, w = gray.shape
    scale = min(1.0, max_edge / max(w, h))
    if scale >= 1.0:
        return gray, 1.0
    nw = max(1, int(js_round(w * scale)))
    nh = max(1, int(js_round(h * scale)))
    ys = np.minimum(h - 1, (np.arange(nh) / scale).astype(np.int32))
    xs = np.minimum(w - 1, (np.arange(nw) / scale).astype(np.int32))
    return gray[ys][:, xs].copy(), scale


def _label_components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    count = 0
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or labels[y, x]:
                continue
            count += 1
            stack = [(y, x)]
            labels[y, x] = count
            while stack:
                cy, cx = stack.pop()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if ny < 0 or nx < 0 or ny >= h or nx >= w:
                            continue
                        if not mask[ny, nx] or labels[ny, nx]:
                            continue
                        labels[ny, nx] = count
                        stack.append((ny, nx))
    return labels, count


def _build_bubble_mask(gray: np.ndarray, s: dict) -> np.ndarray:
    h, w = gray.shape
    bright = gray >= s["bright_thr"]
    labels, count = _label_components(bright)
    bubble = np.zeros((h, w), dtype=np.float64)
    if count == 0:
        return bubble
    area_img = float(h * w)
    for lab in range(1, count + 1):
        ys, xs = np.where(labels == lab)
        a = int(ys.size)
        frac = a / area_img
        if frac < s["min_bubble_area_frac"] or frac > s["max_bubble_area_frac"]:
            continue
        minx, maxx = int(xs.min()), int(xs.max())
        miny, maxy = int(ys.min()), int(ys.max())
        bw = maxx - minx + 1
        bh = maxy - miny + 1
        if bw < s["min_bubble_side"] or bh < s["min_bubble_side"]:
            continue
        if bw >= s["bg_width_frac"] * w and bh >= s["bg_height_frac"] * h:
            continue
        fill = a / (bw * bh)
        if fill < s["min_fill"]:
            continue
        sub = gray[miny : maxy + 1, minx : maxx + 1]
        if float((sub < 160).mean()) < s["min_dark_in_bubble"]:
            continue
        bubble[labels == lab] = 1.0
    return bubble


def _best_window_1d(
    line: np.ndarray, min_keep: int, min_bubble_reduce: float
) -> tuple[int, int, float]:
    """line[i] = bubble dens hàng/cột i.

    Ưu tiên cửa sổ DÀI (ít cắt): duyệt length từ dài → ngắn, nhận length đầu tiên
    có start nào giảm bubble ≥ min_bubble_reduce so với full. Trong cùng length
    chọn start bubble-mean thấp nhất.
    """
    n = len(line)
    full_mean = float(line.mean()) if n else 0.0
    if n <= min_keep:
        return 0, n, full_mean
    ps = np.zeros(n + 1, dtype=np.float64)
    ps[1:] = np.cumsum(line)
    # dài → ngắn: 100%, 92%, 85%, 78%, min_keep
    lengths = sorted(
        {
            n,
            int(js_round(n * 0.92)),
            int(js_round(n * 0.85)),
            int(js_round(n * 0.78)),
            min_keep,
        },
        reverse=True,
    )
    for length in lengths:
        if length < min_keep or length > n or length == n:
            continue
        best_start = 0
        best_mean = float("inf")
        for start in range(0, n - length + 1):
            mean = (ps[start + length] - ps[start]) / length
            if mean < best_mean:
                best_mean = mean
                best_start = start
        if full_mean <= 1e-12:
            continue
        if (full_mean - best_mean) / full_mean >= min_bubble_reduce:
            return best_start, length, float(best_mean)
    return 0, n, full_mean


def auto_region(rgb: np.ndarray, **overrides: Any) -> dict | None:
    """Ảnh RGB(A) → region full-width (manhwa) cắt dọc né bubble, hoặc None."""
    s = {**AUTO_REGION_DEFAULTS, **overrides}
    if rgb.ndim != 3 or rgb.shape[0] < 8 or rgb.shape[1] < 8:
        return None
    h0, w0 = rgb.shape[:2]
    gray0 = to_gray(rgb)
    gray, scale = _downscale_gray(gray0, int(s["max_edge"]))
    h, w = gray.shape

    bubble = _build_bubble_mask(gray, s)
    bubble_frac_full = float(bubble.mean())
    if bubble_frac_full < 0.012:
        return None

    tall = h >= w * 1.05  # band dọc / vuông-dọc → chỉ crop trục dọc
    wide = w > h * 1.15

    x0, y0, rw, rh = 0, 0, w, h
    sub_bub = bubble_frac_full

    if tall or not wide:
        # Full width, chọn cửa sổ dọc
        row_bub = bubble.mean(axis=1)
        min_keep_h = max(8, int(js_round(h * s["min_keep_h_frac"])))
        y0, rh, sub_bub = _best_window_1d(row_bub, min_keep_h, s["min_bubble_reduce"])
        x0, rw = 0, w
    else:
        # Ảnh ngang: full height, cửa sổ ngang
        col_bub = bubble.mean(axis=0)
        min_keep_w = max(8, int(js_round(w * s["min_keep_w_frac"])))
        x0, rw, sub_bub = _best_window_1d(col_bub, min_keep_w, s["min_bubble_reduce"])
        y0, rh = 0, h

    trim = 1.0 - (rw * rh) / (w * h)
    if trim < s["min_trim_frac"]:
        return None
    if bubble_frac_full > 0 and (bubble_frac_full - sub_bub) / bubble_frac_full < s[
        "min_bubble_reduce"
    ]:
        return None

    inv = 1.0 / scale if scale > 0 else 1.0
    x = x0 * inv
    y = y0 * inv
    rw_o = rw * inv
    rh_o = rh * inv
    pad_x = max(int(s["pad_min_px"]), int(js_round(rw_o * s["pad_pct"])))
    pad_y = max(int(s["pad_min_px"]), int(js_round(rh_o * s["pad_pct"])))
    # Full-width manhwa: không pad ngang làm lệch — pad dọc thôi
    if tall or not wide:
        x, rw_o = 0.0, float(w0)
        y = max(0.0, y - pad_y)
        rh_o = min(h0 - y, rh_o + 2 * pad_y)
    else:
        x = max(0.0, x - pad_x)
        y = 0.0
        rh_o = float(h0)
        rw_o = min(w0 - x, rw_o + 2 * pad_x)
    return _clamp_region({"x": x, "y": y, "w": rw_o, "h": rh_o}, w0, h0)


def auto_region_within(
    rgb: np.ndarray, base: dict | None = None, **overrides: Any
) -> dict | None:
    """Auto trong base (shot-split); không cải thiện → giữ base nếu có."""
    h0, w0 = rgb.shape[:2]
    full = {"x": 0, "y": 0, "w": w0, "h": h0}
    box = _clamp_region(base or full, w0, h0)
    bx, by, bw, bh = box["x"], box["y"], box["w"], box["h"]
    crop = rgb[by : by + bh, bx : bx + bw]
    local = auto_region(crop, **overrides)
    if local is None:
        return dict(box) if base is not None else None
    return _clamp_region(
        {"x": bx + local["x"], "y": by + local["y"], "w": local["w"], "h": local["h"]},
        w0,
        h0,
    )
