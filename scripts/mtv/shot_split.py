# Port từ content-extractor/lib/shot-split.ts — đồng bộ 2 nơi khi fix bug.
#
# Cắt shot quá cao thành nhiều shot ngắn hơn, SAU khi kịch bản đã xong.
#
# Band do ingest cắt ra được tối ưu cho vision (trọn nhân vật + bong bóng thoại của nó),
# nên thường cao gấp 2–3 lần bề rộng. Chiếu nguyên band vào khung ngang thì ảnh chỉ chiếm
# ~29% bề rộng, còn lia dọc để lấp khung thì người xem phải đuổi theo ảnh chạy. Cắt band
# thành mảnh thấp hơn giải quyết cả hai: mỗi mảnh đứng yên mà vẫn to.
#
# Cắt tại GUTTER bằng chính thuật toán ingest (split_gray) chứ không chia đều — chia đều
# sẽ xẻ ngang mặt nhân vật và bong bóng thoại. Khác ingest ở đúng một chỗ: trần thấp hơn.
#
# Trần tính bằng PIXEL, không theo tỉ lệ khung — cùng một bản cắt dùng được cho mọi aspect.
#
# Thời lượng tự khớp: allocate_shots chia audio của đoạn cho các shot theo weight, nên
# thêm shot chỉ làm mỗi shot ngắn lại, tổng không đổi.

from __future__ import annotations

import dataclasses
import os

import numpy as np

from .smart_split import SplitSettings, split_gray

# Hai số này chốt BẰNG MẮT trên chương thật (shot-split.ts:29-42): cắt dè hơn thì ảnh nhỏ
# hơn và còn cảnh lia dọc, nhưng dễ xem hơn hẳn so với cắt sâu (1.2s/shot, hay đứt khung).
SHOT_MAX_HEIGHT_DEFAULT = 1100
# Sàn 0.6× như ingest — merge_low_ink_bands gộp mảnh cụt lại, giữ mảnh nguyên vẹn. Đây là
# hành vi MONG MUỐN, không phải rò rỉ cần vá (shot-split.ts:43-48).
MIN_BAND_RATIO = 0.6


def read_shot_split_settings() -> SplitSettings:
    raw = os.environ.get("SHOT_MAX_HEIGHT", "")
    try:
        max_band_height = int(raw)
    except ValueError:
        max_band_height = SHOT_MAX_HEIGHT_DEFAULT
    if max_band_height < 1:
        max_band_height = SHOT_MAX_HEIGHT_DEFAULT
    return dataclasses.replace(
        SplitSettings(),
        min_band_height=round(MIN_BAND_RATIO * max_band_height),
        max_band_height=max_band_height,
    )


def crop_gray(gray: np.ndarray, r: dict) -> np.ndarray:
    """Ảnh xám giới hạn trong region — split_gray cần toạ độ từ 0 nên phải cắt thật."""
    return gray[r["y"]: r["y"] + r["h"], r["x"]: r["x"] + r["w"]]


def split_shot(shot: dict, gray: np.ndarray, s: SplitSettings) -> list[dict]:
    """Một shot → 1..N shot. Không cần cắt thì trả chính shot đó, không đụng dữ liệu."""
    height, width = gray.shape
    rect = shot.get("region") or {"x": 0, "y": 0, "w": width, "h": height}
    if rect["h"] <= s.max_band_height:
        return [shot]

    parts = split_gray(crop_gray(gray, rect), s)
    if len(parts) <= 1:
        return [shot]

    out = []
    for p in parts:
        # split_gray trả toạ độ trong khung region → dời về toạ độ ảnh gốc.
        out.append({
            **shot,
            "region": {"x": rect["x"] + p["x"], "y": rect["y"] + p["y"],
                       "w": p["w"], "h": p["h"]},
            "weight": shot["weight"] * p["h"] / rect["h"],
        })
    return out


def split_tall_shots(segments: list[dict], gray_of, s: SplitSettings | None = None) -> list[dict]:
    """Nở mọi shot quá cao. gray_of(source_index) → ảnh xám, trả None thì giữ nguyên shot."""
    s = s or read_shot_split_settings()
    out = []
    for seg in segments:
        shots = []
        for shot in seg["shots"]:
            gray = gray_of(shot["source_index"])
            shots.extend(split_shot(shot, gray, s) if gray is not None else [shot])
        out.append({**seg, "shots": shots})
    return out
