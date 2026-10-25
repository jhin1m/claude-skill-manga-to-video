#!/usr/bin/env python3
# Port từ content-extractor/scripts/render-video.mts (orchestration :140-260) — đồng bộ
# 2 nơi khi fix bug. Luồng: đọc script + audio-manifest → buildTimeline → SRT/timeline.json
# → render từng shot (Ken Burns) → concat → audio track (adelay theo start+padHead) →
# BGM duck → burn subs → mux → out/video.mp4.
# Hợp đồng skill: shots full-slice → split_tall → auto-region né bubble (tắt bằng --no-auto-region).
# Hiệu ứng toàn cục qua --motion (mặc định static); agent không chỉ định region/motion trong
# script.json.
#
# CLI: render.py --workdir W --aspect 9:16|16:9|1:1 [--fps 30] [--zoom 1.12] [--bgm FILE]
#                [--bgm-volume 0.3] [--duck-db -12] [--subs srt|burn|both|none] [--dry-run]
#                [--motion static|auto] [--fill cover|blur]
#                [--no-auto-region] [--subs-preset ID] [--subs-style JSON] [--subs-fonts-dir D]

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
from mtv.auto_region import auto_region_within
from mtv.subtitle_style import preset_ids, resolve_subtitle_style, warn_font
from mtv.kenburns import default_fill, kenburns, resolve_shot_motion
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


def _slice_loaders(workdir: Path, manifest: dict):
    """gray_of + rgb_of (nạp lười) cho split_tall + auto_region."""
    from PIL import Image  # PIL chỉ cần khi thực sự cắt — import tại chỗ, không nặng CLI
    import numpy as np

    gray_cache: dict[int, object] = {}
    rgb_cache: dict[int, object] = {}

    def rgb_of(source_index: int):
        if source_index not in rgb_cache:
            path = workdir / manifest["slices"][source_index]["file"]
            with Image.open(path) as img:
                rgb_cache[source_index] = np.asarray(img.convert("RGB"))
        return rgb_cache[source_index]

    def gray_of(source_index: int):
        if source_index not in gray_cache:
            gray_cache[source_index] = to_gray(rgb_of(source_index))
        return gray_cache[source_index]

    return gray_of, rgb_of


def apply_auto_regions(segments: list[dict], rgb_of) -> list[dict]:
    """Gán region né bubble cho từng shot (trong base region nếu đã có từ split_tall)."""
    out = []
    n_set = 0
    for seg in segments:
        shots = []
        for shot in seg["shots"]:
            rgb = rgb_of(shot["source_index"])
            if rgb is None:
                shots.append(shot)
                continue
            region = auto_region_within(rgb, shot.get("region"))
            if region is not None:
                n_set += 1
                shots.append({**shot, "region": region})
            else:
                shots.append(shot)
        out.append({**seg, "shots": shots})
    print(f"[render] auto-region: {n_set} shot có khung né bubble")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Render video mp4 + SRT từ workdir (script + audio)")
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--aspect", required=True, choices=["9:16", "16:9", "1:1"])
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--zoom", type=float, default=DEFAULT_ZOOM)
    parser.add_argument("--bgm", type=Path, default=None)
    parser.add_argument("--bgm-volume", type=float, default=0.3)
    parser.add_argument("--duck-db", type=float, default=-12)
    parser.add_argument(
        "--subs",
        choices=["srt", "burn", "both", "none"],
        default="both",
        help="srt = chỉ file .srt rời · burn/both = cháy chữ vào video · none = tắt hẳn phụ đề",
    )
    parser.add_argument(
        "--motion",
        choices=["static", "auto"],
        default="static",
        help="static = mọi shot đứng yên (mặc định) · auto = lia/zoom tự suy theo hình dáng shot",
    )
    parser.add_argument(
        "--fill",
        choices=["cover", "blur"],
        default=None,
        help="shot lệch tỉ lệ khung: cover = full màn hình, cắt mép · blur = vừa khung trên nền "
             "blur. Mặc định: cover với 9:16, blur với khung khác",
    )
    parser.add_argument(
        "--subs-preset",
        default=None,
        choices=preset_ids() or None,
        help="preset style phụ đề dựng sẵn, khớp từng field với bản web",
    )
    parser.add_argument(
        "--subs-style",
        default=None,
        help='JSON style phụ đề, đè lên preset (hoặc lên mặc định nếu không có preset). Ví dụ: '
             '\'{"font":"Kanit","bold":true,"uppercase":true,"border":"box",'
             '"outline_color":"#000000","outline_opacity":0.6}\'',
    )
    parser.add_argument(
        "--subs-fonts-dir",
        default=None,
        help="thư mục font cho libass (mặc định lấy SUBS_FONTS_DIR)",
    )
    parser.add_argument("--dry-run", action="store_true", help="in timeline + lệnh ffmpeg, không chạy")
    parser.add_argument(
        "--no-auto-region",
        action="store_true",
        help="tắt auto-crop né bubble (mặc định BẬT)",
    )
    args = parser.parse_args()

    # Preset kiểm NGAY: thiếu schemas/parity.json thì argparse không có `choices` để chặn, mà
    # để lỗi rơi xuống sau split_tall + auto-region là đốt vài phút nạp slice vô ích.
    if args.subs_preset:
        try:
            resolve_subtitle_style(args.subs_preset)
        except (FileNotFoundError, KeyError) as e:
            fail("subs-preset-invalid", str(e))

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

    # ScriptSegment nội bộ: shots full-slice (region null, motion theo --motion, weight 1)
    segments = [
        {
            "id": seg["id"],
            "order": seg["order"],
            "narration": seg["narration"],
            "shots": [
                {"source_index": idx, "region": None,
                 "motion": resolve_shot_motion(None, args.motion), "weight": 1}
                for idx in seg["shot_indices"]
            ],
        }
        for seg in script["segments"]
    ]
    # Band cao hơn trần → cắt thành nhiều shot ngắn tại gutter. Chạy TRƯỚC build_timeline
    # để thời lượng đoạn chia đúng cho số shot sau khi nở. Hợp đồng script.json không đổi:
    # region là chuyện hình ảnh, suy ra từ ảnh chứ không do agent chỉ định.
    gray_of, rgb_of = _slice_loaders(args.workdir, manifest)
    segments = split_tall_shots(segments, gray_of)
    # Auto-crop né bubble (mặc định bật) — chỉ đổi framing, không sửa pixel slice.
    if not args.no_auto_region:
        segments = apply_auto_regions(segments, rgb_of)
    seg_durations = {
        seg_id: info["duration_sec"] for seg_id, info in audio_manifest["segments"].items()
    }

    timeline = build_timeline(segments, seg_durations)
    # Style phụ đề: mặc định ◀ preset ◀ --subs-style, mọi giá trị đi qua clamp. Dùng cho CẢ
    # .srt (uppercase/ngắt dòng) lẫn burn-in để hai đầu ra không lệch nhau.
    override = {}
    if args.subs_style:
        try:
            override = json.loads(args.subs_style)
        except json.JSONDecodeError as e:
            fail("subs-style-invalid", f"--subs-style không phải JSON hợp lệ: {e}")
            return
        if not isinstance(override, dict):
            fail("subs-style-invalid", "--subs-style phải là object JSON")
            return
    try:
        subs_style = resolve_subtitle_style(args.subs_preset, override)
    except (FileNotFoundError, KeyError) as e:
        fail("subs-preset-invalid", str(e))
        return
    if args.subs != "none":
        warn_font(subs_style)
    odir = out_dir(args.workdir)
    odir.mkdir(parents=True, exist_ok=True)
    srt_path = odir / "video.srt"
    if args.subs == "none":
        # Tắt hẳn phụ đề: dọn .srt của lần render trước để out/ không còn phụ đề lệch video
        srt_path.unlink(missing_ok=True)
    else:
        srt_path.write_text(to_srt(timeline, segments, subs_style), encoding="utf-8")
    (odir / "timeline.json").write_text(
        json.dumps(to_timeline_json(timeline, segments), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    srt_note = "" if args.subs == "none" else ", video.srt"
    print(f"[render] timeline {timeline['total_sec']:.2f}s → {odir}/timeline.json{srt_note}")

    # Chuẩn bị lệnh render từng shot. Temp dir namespace theo aspect+pid — hai render song
    # song trên cùng workdir không giẫm shot của nhau (mẫu render-video.mts workDir riêng)
    slices = manifest["slices"]
    shots_dir = odir / f".shots-{args.aspect.replace(':', 'x')}-{os.getpid()}"
    shot_cmds: list[list[str]] = []
    shot_files: list[str] = []
    fill = args.fill or default_fill(args.aspect)
    print(f"[render] hiệu ứng {args.motion}, lấp khung {fill}")
    for si, tseg in enumerate(timeline["segments"]):
        for shot_idx, shot in enumerate(tseg["shots"]):
            s = slices[shot["source_index"]]
            duration_sec = shot["end"] - shot["start"]
            filter_graph = kenburns({
                "img_w": s["w"], "img_h": s["h"], "region": shot["region"],
                "motion": shot["motion"], "aspect": args.aspect,
                "duration_sec": duration_sec, "fps": args.fps, "zoom": args.zoom,
                "fill": fill,
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

        # .srt rời được giữ ở mọi mode trừ none (kể cả mode burn)
        video_path = silent_path
        if args.subs in ("burn", "both"):
            if has_filter("subtitles"):
                burned_path = str(shots_dir / "burned.mp4")
                burn_subs(silent_path, str(srt_path), subs_style, burned_path, args.subs_fonts_dir)
                video_path = burned_path
                print("[render] phụ đề đã burn-in")
            else:
                # Nhiều build ffmpeg (vd bottle brew hiện tại) không kèm libass. Bản web
                # content-extractor tự rơi xuống ffmpeg-static; skill này không có
                # node_modules nên chỉ báo cách xử lý.
                print("[render] CẢNH BÁO: ffmpeg thiếu filter subtitles (libass) — bỏ qua burn-in, "
                      "dùng .srt rời. Cài bản có libass, hoặc trỏ PATH tới bản ffmpeg khác "
                      "(kiểm bằng: ffmpeg -filters | grep subtitles).", file=sys.stderr)

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
