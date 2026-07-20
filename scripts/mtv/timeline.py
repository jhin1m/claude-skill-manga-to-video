# Port từ content-extractor/lib/timeline.ts — đồng bộ 2 nơi khi fix bug.
# Bất biến sync: audio TTS là "nhạc trưởng" — narration khớp cứng với SEGMENT; audio
# segment phân bổ cho các shot theo weight → timeline.json ≡ timeline video.

from __future__ import annotations

import math
import re

from .js_math import js_round
from .schema_defs import estimate_seconds

TIMELINE_DEFAULTS = {
    "pad_head_sec": 0.15,  # đệm trước narration
    "pad_tail_sec": 0.35,  # đệm sau narration (thở giữa hai đoạn)
    "floor_sec": 2.0,  # sàn thời lượng segment
    "shot_floor_sec": 0.8,  # sàn thời lượng một shot
}


def round3(n: float) -> float:
    return js_round(n * 1000) / 1000


def allocate_shots(shots: list[dict], duration_sec: float, shot_floor_sec: float) -> list[float]:
    """Chia dur cho các shot theo weight (chuẩn hoá), mỗi shot ≥ sàn (timeline.ts:46-73)."""
    if not shots:
        return []
    if len(shots) * shot_floor_sec >= duration_sec:
        return [duration_sec / len(shots)] * len(shots)
    total_weight = sum(max(0, s["weight"]) for s in shots)
    if total_weight > 0:
        weights = [max(0, s["weight"]) / total_weight for s in shots]
    else:
        weights = [1 / len(shots)] * len(shots)
    durations = [w * duration_sec for w in weights]
    # Áp sàn: shot hụt kéo lên sàn, phần dư trừ tỉ lệ vào các shot còn dư địa
    for _ in range(len(shots)):
        deficit = sum(max(0, shot_floor_sec - d) for d in durations)
        if deficit <= 1e-9:
            break
        donor_total = sum(d - shot_floor_sec for d in durations if d > shot_floor_sec)
        durations = [
            shot_floor_sec if d < shot_floor_sec
            else d if donor_total <= 0
            else d - ((d - shot_floor_sec) / donor_total) * deficit
            for d in durations
        ]
    return durations


def build_timeline(
    segments: list[dict],
    seg_durations: dict[str, float],
    segment_overrides: dict[str, dict] | None = None,
    opts: dict | None = None,
) -> dict:
    """segments: [{id, order, narration, shots: [{source_index, region, motion, weight}]}].
    seg_durations: seg_id → giây audio TTS (0 = không audio → durationHint/floor)."""
    o = {**TIMELINE_DEFAULTS, **(opts or {})}
    out = []
    cursor = 0.0
    for seg in sorted(segments, key=lambda s: s["order"]):
        override = ((segment_overrides or {}).get(seg["id"]) or {}).get("duration_sec")
        audio_sec = seg_durations.get(seg["id"], 0)
        if override is not None and override > 0:
            # Audio là nhạc trưởng: override không được ngắn hơn audio + pad
            dur = max(override, audio_sec + o["pad_head_sec"] + o["pad_tail_sec"]) if audio_sec > 0 else override
        elif audio_sec > 0:
            dur = max(audio_sec + o["pad_head_sec"] + o["pad_tail_sec"], o["floor_sec"])
        else:
            # ?? của TS: chỉ fallback khi duration_hint vắng mặt (0 hợp lệ vẫn được giữ)
            hint = seg.get("duration_hint")
            if hint is None:
                hint = estimate_seconds(seg["narration"])
            dur = max(hint, o["floor_sec"])
        dur = round3(dur)
        shot_durations = allocate_shots(seg["shots"], dur, o["shot_floor_sec"])
        shots = []
        shot_cursor = cursor
        for i, shot in enumerate(seg["shots"]):
            end = cursor + dur if i == len(seg["shots"]) - 1 else shot_cursor + shot_durations[i]
            shots.append({
                "source_index": shot["source_index"],
                "region": dict(shot["region"]) if shot.get("region") else None,
                "motion": dict(shot["motion"]) if shot.get("motion") else None,
                "start": round3(shot_cursor),
                "end": round3(end),
            })
            shot_cursor = end
        out.append({"seg_id": seg["id"], "start": round3(cursor), "end": round3(cursor + dur), "shots": shots})
        cursor = round3(cursor + dur)
    return {"segments": out, "total_sec": round3(cursor)}


def split_sentences(text: str) -> list[str]:
    """Tách câu theo dấu kết câu (. ! ? …) + xuống dòng, giữ dấu cuối (timeline.ts:125-131)."""
    out = []
    for line in re.split(r"\n+", text):
        for s in re.split(r"(?<=[.!?…])\s+", line):
            s = s.strip()
            if s:
                out.append(s)
    return out


def srt_time(sec: float) -> str:
    clamped = max(0.0, sec)
    h = int(clamped // 3600)
    m = int((clamped % 3600) // 60)
    s = int(clamped % 60)
    ms = js_round((clamped - math.floor(clamped)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def allocate_captions(sentences: list[str], start_sec: float, end_sec: float) -> list[dict]:
    """Canh giờ các câu trong [start, end] tỉ lệ theo số ký tự (timeline.ts:145-162)."""
    if not sentences:
        return []
    total_chars = sum(len(t) for t in sentences)
    span = max(0.0, end_sec - start_sec)
    out = []
    cursor = start_sec
    for i, text in enumerate(sentences):
        share = len(text) / total_chars if total_chars > 0 else 1 / len(sentences)
        end = end_sec if i == len(sentences) - 1 else cursor + share * span
        out.append({"text": text, "start": round3(cursor), "end": round3(end)})
        cursor = end
    return out


def to_srt(timeline: dict, segments: list[dict]) -> str:
    """Caption tách theo CÂU trong từng segment — mọi caption nằm gọn trong segment."""
    by_id = {s["id"]: s for s in segments}
    blocks = []
    index = 1
    for tseg in timeline["segments"]:
        seg = by_id.get(tseg["seg_id"])
        if not seg or not seg["narration"].strip():
            continue
        for cap in allocate_captions(split_sentences(seg["narration"]), tseg["start"], tseg["end"]):
            blocks.append(f"{index}\n{srt_time(cap['start'])} --> {srt_time(cap['end'])}\n{cap['text']}\n")
            index += 1
    return "\n".join(blocks)


def to_timeline_json(timeline: dict, segments: list[dict]) -> dict:
    by_id = {s["id"]: s for s in segments}
    return {
        "totalSec": timeline["total_sec"],
        "segments": [
            {
                "order": by_id.get(t["seg_id"], {}).get("order", i),
                "segId": t["seg_id"],
                "narration": by_id.get(t["seg_id"], {}).get("narration", ""),
                "start": t["start"],
                "end": t["end"],
                "shots": t["shots"],
            }
            for i, t in enumerate(timeline["segments"])
        ],
    }
