#!/usr/bin/env python3
# Port từ content-extractor/scripts/render-video.mts (orchestration :140-260) — đồng bộ
# 2 nơi khi fix bug. Luồng: đọc script + audio-manifest → buildTimeline → SRT/timeline.json
# → render từng shot (Ken Burns) → concat → audio track (adelay theo start+padHead) →
# BGM duck → burn subs → mux → out/video.mp4.
# Hợp đồng skill v1: shots full-slice, region null, motion auto (agent không chỉ định).
#
# CLI: render.py --workdir W --aspect 9:16|16:9|1:1 [--fps 30] [--zoom 1.12] [--bgm FILE]
#                [--bgm-volume 0.3] [--duck-db -12] [--subs srt|burn|both] [--dry-run]

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtv.ffmpeg_cmd import (
    SUBTITLE_STYLE_DEFAULTS,
    build_audio_track,
    burn_subs,
    concat_video,
    duck_bgm,
    has_filter,
    mux,
    probe_streams,
    render_shot_cmd,
    run_command,
)
from mtv.kenburns import kenburns
from mtv.shot_split import split_tall_shots
from mtv.smart_split import to_gray
from mtv.schema_defs import (
    audio_dir,
    audio_manifest_path,
    load_json,
    load_manifest,
    out_dir,
    save_manifest,
    script_path,
)
from mtv.timeline import TIMELINE_DEFAULTS, build_timeline, to_srt, to_timeline_json

DEFAULT_ZOOM = 1.12  # default TS RenderConfig (lib/schema.ts:291)


def fail(rule: str, message: str) -> None:
    print(json.dumps({"file": "render", "path": "$", "rule": rule, "message": message}, ensure_ascii=False))
    sys.exit(1)


def _slice_gray_loader(workdir: Path, manifest: dict):
    """gray_of(source_index) cho split_tall_shots — nạp lười, mỗi slice giải mã một lần."""
    from PIL import Image  # PIL chỉ cần khi thực sự cắt — import tại chỗ, không nặng CLI
    import numpy as np

    cache: dict[int, object] = {}

    def gray_of(source_index: int):
        if source_index not in cache:
            path = workdir / manifest["slices"][source_index]["file"]
            with Image.open(path) as img:
                cache[source_index] = to_gray(np.asarray(img.convert("RGB")))
        return cache[source_index]

    return gray_of


def main() -> None:
    parser = argparse.ArgumentParser(description="Render video mp4 + SRT từ workdir (script + audio)")
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--aspect", required=True, choices=["9:16", "16:9", "1:1"])
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--zoom", type=float, default=DEFAULT_ZOOM)
    parser.add_argument("--bgm", type=Path, default=None)
    parser.add_argument("--bgm-volume", type=float, default=0.3)
    parser.add_argument("--duck-db", type=float, default=-12)
    parser.add_argument("--subs", choices=["srt", "burn", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true", help="in timeline + lệnh ffmpeg, không chạy")
    args = parser.parse_args()

    # validate script trước khi render
    validate = Path(__file__).resolve().parent / "validate.py"
    proc = subprocess.run(
        [sys.executable, str(validate), "--workdir", str(args.workdir), "--stage", "script"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.exit(1)

    manifest = load_manifest(args.workdir)
    script = load_json(script_path(args.workdir))
    amanifest_path = audio_manifest_path(args.workdir)
    if not amanifest_path.exists():
        fail("audio-manifest-missing", "chưa có audio-manifest.json — chạy tts.py trước")
    audio_manifest = load_json(amanifest_path)

    # ScriptSegment nội bộ: shots full-slice (region null, motion auto, weight 1)
    segments = [
        {
            "id": seg["id"],
            "order": seg["order"],
            "narration": seg["narration"],
            "shots": [
                {"source_index": idx, "region": None, "motion": None, "weight": 1}
                for idx in seg["shot_indices"]
            ],
        }
        for seg in script["segments"]
    ]
    # Band cao hơn trần → cắt thành nhiều shot ngắn tại gutter. Chạy TRƯỚC build_timeline
    # để thời lượng đoạn chia đúng cho số shot sau khi nở. Hợp đồng script.json không đổi:
    # region là chuyện hình ảnh, suy ra từ ảnh chứ không do agent chỉ định.
    segments = split_tall_shots(segments, _slice_gray_loader(args.workdir, manifest))
    seg_durations = {
        seg_id: info["duration_sec"] for seg_id, info in audio_manifest["segments"].items()
    }

    timeline = build_timeline(segments, seg_durations)
    srt = to_srt(timeline, segments)

    odir = out_dir(args.workdir)
    odir.mkdir(parents=True, exist_ok=True)
    srt_path = odir / "video.srt"
    srt_path.write_text(srt, encoding="utf-8")
    (odir / "timeline.json").write_text(
        json.dumps(to_timeline_json(timeline, segments), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[render] timeline {timeline['total_sec']:.2f}s → {odir}/timeline.json, video.srt")

    # Chuẩn bị lệnh render từng shot. Temp dir namespace theo aspect+pid — hai render song
    # song trên cùng workdir không giẫm shot của nhau (mẫu render-video.mts workDir riêng)
    slices = manifest["slices"]
    shots_dir = odir / f".shots-{args.aspect.replace(':', 'x')}-{os.getpid()}"
    shot_cmds: list[list[str]] = []
    shot_files: list[str] = []
    for si, tseg in enumerate(timeline["segments"]):
        for shot_idx, shot in enumerate(tseg["shots"]):
            s = slices[shot["source_index"]]
            duration_sec = shot["end"] - shot["start"]
            filter_graph = kenburns({
                "img_w": s["w"], "img_h": s["h"], "region": shot["region"],
                "motion": shot["motion"], "aspect": args.aspect,
                "duration_sec": duration_sec, "fps": args.fps, "zoom": args.zoom,
            })
            out_file = str(shots_dir / f"shot-{si:03d}-{shot_idx}.mp4")
            shot_cmds.append(render_shot_cmd(
                str(args.workdir / s["file"]), filter_graph, duration_sec, args.fps, out_file
            ))
            shot_files.append(out_file)

    if args.dry_run:
        print(f"[dry-run] {len(shot_cmds)} shot:")
        for cmd in shot_cmds:
            print("  " + shlex.join(cmd))
        print("[dry-run] tiếp theo: concat → build_audio_track → "
              + ("duck_bgm → " if args.bgm else "") +
              ("burn_subs → " if args.subs in ("burn", "both") else "") + "mux → out/video.mp4")
        return

    if args.bgm and not args.bgm.exists():
        fail("bgm-missing", f"file BGM không tồn tại: {args.bgm}")

    shots_dir.mkdir(parents=True, exist_ok=True)
    try:
        for i, cmd in enumerate(shot_cmds):
            run_command(cmd)
        print(f"[render] {len(shot_cmds)} shot xong")

        silent_path = str(shots_dir / "silent.mp4")
        concat_video(shot_files, silent_path)

        # Narration đặt sau padHead của segment — khớp buildTimeline (render-video.mts:207-218)
        adir = audio_dir(args.workdir)
        seg_audio = []
        for tseg in timeline["segments"]:
            info = audio_manifest["segments"].get(tseg["seg_id"])
            if info and info.get("file"):
                seg_audio.append({
                    "path": str(adir / info["file"]),
                    "start_sec": tseg["start"] + TIMELINE_DEFAULTS["pad_head_sec"],
                })
        narration_path = str(shots_dir / "narration.wav")
        build_audio_track(seg_audio, timeline["total_sec"], narration_path)

        audio_path = narration_path
        if args.bgm:
            mixed_path = str(shots_dir / "mixed.wav")
            duck_bgm(narration_path, str(args.bgm), args.bgm_volume, args.duck_db,
                     timeline["total_sec"], mixed_path)
            audio_path = mixed_path
            print(f"[render] BGM duck ({args.duck_db}dB, volume {args.bgm_volume})")

        # .srt rời LUÔN được giữ (kể cả mode burn) — hợp đồng "luôn xuất .srt"
        video_path = silent_path
        if args.subs in ("burn", "both"):
            if has_filter("subtitles"):
                burned_path = str(shots_dir / "burned.mp4")
                burn_subs(silent_path, str(srt_path), SUBTITLE_STYLE_DEFAULTS, burned_path)
                video_path = burned_path
                print("[render] phụ đề đã burn-in")
            else:
                print("[render] CẢNH BÁO: ffmpeg thiếu filter subtitles (libass) — bỏ qua burn-in, "
                      "dùng .srt rời. Cài bản đầy đủ: brew install ffmpeg.", file=sys.stderr)

        final_path = odir / "video.mp4"
        mux(video_path, audio_path, str(final_path))
        streams = probe_streams(str(final_path))
        if "video" not in streams or "audio" not in streams:
            fail("output-invalid", f"output thiếu stream (có: {streams})")
        print(f"[render] XONG → {final_path} ({timeline['total_sec']:.2f}s)")
    except RuntimeError as err:
        fail("ffmpeg-error", str(err))
    finally:
        shutil.rmtree(shots_dir, ignore_errors=True)

    manifest = load_manifest(args.workdir)
    manifest["stages"]["render"] = "done"
    save_manifest(args.workdir, manifest)


if __name__ == "__main__":
    main()
