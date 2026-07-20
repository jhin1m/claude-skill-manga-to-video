# Port từ content-extractor/lib/ffmpeg.ts — đồng bộ 2 nơi khi fix bug.
# Wrapper ffmpeg/ffprobe + builder lệnh render: renderShot → concat demuxer →
# buildAudioTrack (adelay/amix) → duckBgm (sidechaincompress) → burnSubs (libass) → mux.

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def run_command(cmd: list[str], timeout: float | None = None) -> str:
    """Chạy lệnh, trả stdout. Lỗi → RuntimeError kèm 6 dòng stderr cuối (ffmpeg.ts:9-34)."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-6:])
        raise RuntimeError(f"{cmd[0]} exit {proc.returncode}:\n{tail}")
    return proc.stdout


_filter_list_cache: str | None = None


def has_filter(name: str) -> bool:
    """Kiểm tra ffmpeg build có filter (vd sidechaincompress, subtitles) — cache theo process."""
    global _filter_list_cache
    if _filter_list_cache is None:
        proc = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True)
        _filter_list_cache = proc.stdout
    return bool(re.search(rf"\s{re.escape(name)}\s", _filter_list_cache))


def probe_duration_sec(file_path: str) -> float:
    """Độ dài (giây) file media — nguồn duy nhất cho cả TTS và render (ffmpeg.ts:52-67)."""
    out = run_command(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    )
    try:
        sec = float(out.strip())
    except ValueError:
        sec = float("nan")
    if not (sec == sec and sec >= 0 and sec != float("inf")):  # finite ≥ 0
        raise RuntimeError(f"ffprobe không đọc được độ dài: {file_path}")
    return sec


def probe_streams(file_path: str) -> list[str]:
    """Danh sách codec_type (video/audio) — assert đầu ra cuối (ffmpeg.ts:70-81)."""
    out = run_command(
        [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    )
    return [line for line in out.strip().split("\n") if line]


# ==== Builder lệnh render — mỗi hàm trả list args (dry-run in được), *_run chạy luôn ====


def render_shot_cmd(image: str, filter_graph: str, duration_sec: float, fps: int, out: str) -> list[str]:
    """Ảnh tĩnh + filtergraph Ken Burns → đoạn video chuẩn hoá (ffmpeg.ts:85-116)."""
    return [FFMPEG, "-y", "-loop", "1", "-framerate", str(fps), "-i", image,
            "-filter_complex", filter_graph, "-map", "[v]", "-t", f"{duration_sec:.3f}",
            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", out]


def concat_video(shot_files: list[str], out: str) -> None:
    """Nối shot bằng concat demuxer — copy thẳng, không re-encode (ffmpeg.ts:122-141)."""
    list_path = f"{out}.list.txt"
    lines = []
    for f in shot_files:
        escaped = str(Path(f).resolve()).replace("'", "'\\''")  # escape quote đơn cho demuxer
        lines.append(f"file '{escaped}'")
    Path(list_path).write_text("\n".join(lines), encoding="utf-8")
    run_command([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out])


def build_audio_track(seg_audio: list[dict], total_sec: float, out: str) -> None:
    """Track narration: mỗi seg đặt tại start (adelay), amix normalize=0 (ffmpeg.ts:146-191).
    seg_audio: [{path, start_sec}]."""
    if not seg_audio:
        run_command([FFMPEG, "-y", "-f", "lavfi", "-i",
                     f"anullsrc=r=44100:cl=stereo:d={total_sec:.3f}", "-c:a", "pcm_s16le", out])
        return
    args = [FFMPEG, "-y"]
    for seg in seg_audio:
        args += ["-i", seg["path"]]
    from .js_math import js_round

    delayed = ";".join(
        f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{i}]"
        for i, seg in enumerate(seg_audio)
        for ms in [js_round(seg["start_sec"] * 1000)]
    )
    labels = "".join(f"[a{i}]" for i in range(len(seg_audio)))
    if len(seg_audio) == 1:
        mix_or_copy = f"{labels}apad=whole_dur={total_sec:.3f}[aout]"
    else:
        mix_or_copy = f"{labels}amix=inputs={len(seg_audio)}:normalize=0,apad=whole_dur={total_sec:.3f}[aout]"
    args += ["-filter_complex", f"{delayed};{mix_or_copy}", "-map", "[aout]",
             "-t", f"{total_sec:.3f}", "-c:a", "pcm_s16le", out]
    run_command(args)


def duck_bgm(narration_track: str, bgm_path: str, volume: float, duck_db: float, total_sec: float, out: str) -> None:
    """BGM hạ khi có lời qua sidechaincompress; loop khi ngắn (ffmpeg.ts:195-226)."""
    # Map duckDb → ratio nén: -6dB → 4, -12dB → 8, -18dB → 12 (kẹp 2..20)
    ratio = max(2, min(20, abs(duck_db) * (2 / 3)))
    filter_graph = ";".join([
        f"[1:a]atrim=0:{total_sec:.3f},aresample=44100,aformat=channel_layouts=stereo,volume={volume}[bgm]",
        f"[bgm][0:a]sidechaincompress=threshold=0.02:ratio={ratio:.2f}:attack=20:release=400[ducked]",
        "[0:a][ducked]amix=inputs=2:normalize=0[aout]",
    ])
    run_command([FFMPEG, "-y", "-i", narration_track, "-stream_loop", "-1", "-i", bgm_path,
                 "-filter_complex", filter_graph, "-map", "[aout]", "-t", f"{total_sec:.3f}",
                 "-c:a", "pcm_s16le", out])


def _ass_color(hex_color: str) -> str:
    # #rrggbb → &H00BBGGRR (ASS, alpha 00 = đục) — ffmpeg.ts:229-236
    m = re.match(r"^#?([0-9a-f]{6})$", hex_color.strip(), re.IGNORECASE)
    if not m:
        return "&H00FFFFFF"
    rr, gg, bb = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
    return f"&H00{bb}{gg}{rr}".upper()


def _escape_filter_path(p: str) -> str:
    return str(Path(p).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


SUBTITLE_STYLE_DEFAULTS = {
    # defaultRenderConfig (schema.ts:278-286)
    "font": "Be Vietnam Pro",
    "size_pct": 4.5,
    "color": "#ffffff",
    "outline": True,
    "position": "bottom",
    "margin_pct": 8,
}


def burn_subs(video: str, srt_path: str, style: dict, out: str, fonts_dir: str | None = None) -> None:
    """Burn phụ đề theo style — libass canh theo PlayResY=288 (ffmpeg.ts:246-289)."""
    import os

    play_res_y = 288
    font_size = max(8, round((style["size_pct"] / 100) * play_res_y))
    margin_v = round((style["margin_pct"] / 100) * play_res_y)
    alignment = 8 if style["position"] == "top" else 5 if style["position"] == "middle" else 2
    # Lọc ký tự phá cú pháp force_style (' : , \)
    safe_font = re.sub(r"[^\w À-ỹ.-]", "", style["font"]).strip() or "Arial"
    force_style = ",".join([
        f"FontName={safe_font}",
        f"FontSize={font_size}",
        f"PrimaryColour={_ass_color(style['color'])}",
        f"Outline={1 if style['outline'] else 0}",
        "BorderStyle=1",
        f"Alignment={alignment}",
        f"MarginV={margin_v}",
    ])
    d = fonts_dir or os.environ.get("SUBS_FONTS_DIR")
    # ffmpeg 8: filename phải là option CÓ TÊN khi đi kèm force_style
    filter_str = (
        f"subtitles=filename='{_escape_filter_path(srt_path)}':force_style='{force_style}'"
        + (f":fontsdir='{_escape_filter_path(d)}'" if d else "")
    )
    run_command([FFMPEG, "-y", "-i", video, "-vf", filter_str, "-c:v", "libx264",
                 "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", out])


def mux(video: str, audio: str, out: str) -> None:
    """Ghép video + audio → MP4 cuối (video copy, audio AAC) — ffmpeg.ts:292-310."""
    run_command([FFMPEG, "-y", "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a",
                 "-c:v", "copy", "-c:a", "aac", "-shortest", out])
