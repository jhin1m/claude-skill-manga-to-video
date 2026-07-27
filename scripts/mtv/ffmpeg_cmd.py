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


def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    # #rrggbb + độ đục → &HAABBGGRR (ASS: AA=00 đục hoàn toàn, FF trong suốt)
    # Bản TS tương ứng: lib/subtitle-style.ts → assColor()
    alpha = format(round((1 - max(0.0, min(1.0, opacity))) * 255), "02x")
    m = re.match(r"^#?([0-9a-f]{6})$", hex_color.strip(), re.IGNORECASE)
    if not m:
        return f"&H{alpha}FFFFFF".upper()
    rr, gg, bb = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
    return f"&H{alpha}{bb}{gg}{rr}".upper()


def _escape_filter_path(p: str) -> str:
    return str(Path(p).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


# Bản v2, khớp defaultRenderConfig() của schema.ts. Config cũ chỉ có "outline": bool vẫn nhận
# được — _normalize_style() quy đổi sang "border" + "outline_width".
SUBTITLE_STYLE_DEFAULTS = {
    "font": "Be Vietnam Pro",
    "size_pct": 4.5,
    "color": "#ffffff",
    "opacity": 1.0,
    "bold": False,
    "italic": False,
    "underline": False,
    "uppercase": False,
    "letter_spacing": 0,
    "border": "outline",          # none | outline | box
    "outline_width": 2,
    "outline_color": "#000000",
    "outline_opacity": 1.0,
    "shadow_depth": 0,
    "shadow_color": "#000000",
    "position": "bottom",
    "margin_pct": 8,
    "side_margin_pct": 6,
    "max_chars_per_line": 0,
}

# ffmpeg chuyển .srt sang ASS với PlayResX=384 / PlayResY=288 — lề và viền/bóng tính theo
# hai số này (lề ngang theo X, còn lại theo Y).
PLAY_RES_X = 384
PLAY_RES_Y = 288

# Riêng CỠ CHỮ dùng số chia khác: đo thực tế (libass 0.17) cho thấy chữ render nhỏ hơn 1,5 lần
# so với công thức PlayResY=288, nên phải chia 432 thì "x%" mới ra đúng x% chiều cao khung.
# Đồng bộ với FONT_SIZE_DIVISOR trong lib/subtitle-style.ts.
FONT_SIZE_DIVISOR = 432

# CẢNH BÁO: libass parse force_style theo hệ căn lề SSA v4 CŨ, không phải numpad V4+:
#   1 dưới-trái   2 dưới-GIỮA   3 dưới-phải
#   5 trên-trái   6 trên-GIỮA   7 trên-phải
#   9 giữa-trái  10 giữa-GIỮA  11 giữa-phải
# Dùng numpad (top=8, middle=5) sẽ ra "giữa khung căn trái" / "trên căn trái".
_ALIGNMENT = {"top": 6, "middle": 10, "bottom": 2}


def _normalize_style(style: dict) -> dict:
    """Đổ mặc định + quy đổi style v1 (outline: bool) sang v2."""
    s = {**SUBTITLE_STYLE_DEFAULTS, **(style or {})}
    if "outline" in (style or {}) and "border" not in (style or {}):
        s["border"] = "outline" if style["outline"] else "none"
        s["outline_width"] = 2 if style["outline"] else 0
    return s


def burn_subs(video: str, srt_path: str, style: dict, out: str, fonts_dir: str | None = None) -> None:
    """Burn phụ đề theo style — bản Python của lib/subtitle-style.ts + burnSubs().

    Vai trò màu trong ASS (đã đo bằng libass, KHÔNG suy đoán):

                     BorderStyle=1 (viền)   BorderStyle=3 (hộp)
        OutlineColour  màu viền              MÀU HỘP
        BackColour     màu bóng              màu bóng
    """
    import os

    s = _normalize_style(style)
    font_size = max(8, round((s["size_pct"] / 100) * FONT_SIZE_DIVISOR))
    margin_v = round((s["margin_pct"] / 100) * PLAY_RES_Y)
    margin_side = round((s["side_margin_pct"] / 100) * PLAY_RES_X)
    alignment = _ALIGNMENT.get(s["position"], 2)
    border_style = 3 if s["border"] == "box" else 1
    outline = 0 if s["border"] == "none" else s["outline_width"]
    # Lọc ký tự phá cú pháp force_style (' : , \)
    safe_font = re.sub(r"[^\w À-ỹ.-]", "", s["font"]).strip() or "Arial"
    force_style = ",".join([
        f"FontName={safe_font}",
        f"FontSize={font_size}",
        f"PrimaryColour={_ass_color(s['color'], s['opacity'])}",
        f"OutlineColour={_ass_color(s['outline_color'], s['outline_opacity'])}",
        f"BackColour={_ass_color(s['shadow_color'], 1.0)}",
        f"Bold={1 if s['bold'] else 0}",
        f"Italic={1 if s['italic'] else 0}",
        f"Underline={1 if s['underline'] else 0}",
        f"Spacing={s['letter_spacing']}",
        f"BorderStyle={border_style}",
        f"Outline={outline}",
        f"Shadow={s['shadow_depth']}",
        f"Alignment={alignment}",
        f"MarginL={margin_side}",
        f"MarginR={margin_side}",
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
