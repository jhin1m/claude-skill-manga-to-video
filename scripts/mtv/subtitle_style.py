# Port từ content-extractor/lib/subtitle-style.ts — đồng bộ 2 nơi khi fix bug.
# Nguồn DUY NHẤT quy đổi style phụ đề → chuỗi force_style của ASS, tách khỏi ffmpeg_cmd.py
# (bản TS cũng tách: lib/subtitle-style.ts ≠ lib/ffmpeg.ts).
#
# Đơn vị: ffmpeg chuyển .srt sang ASS với PlayResX=384 / PlayResY=288, libass scale theo chiều
# cao video. Nên mọi kích thước tính theo phần trăm khung rồi nhân với PLAY_RES.

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from .js_math import js_round

# Snapshot do repo TS xuất (`pnpm gen:parity`) và ĐÃ COMMIT trong skill này. Nguồn duy nhất
# cho preset + danh sách font, nên preset ở web và ở CLI không thể lệch nhau.
PARITY_FILE = Path(__file__).resolve().parents[2] / "schemas" / "parity.json"

# Bản v2, khớp defaultRenderConfig().subtitles của schema.ts — TRỪ `export`/`burn_in` (cờ UI,
# skill dùng --subs thay). Config cũ chỉ có "outline": bool vẫn nhận được —
# normalize_subtitle_style() quy đổi sang "border" + "outline_width".
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

# Danh sách field ASS mà to_ass_force_style được phép sinh. libass BỎ QUA IM LẶNG field sai tên
# (đo: `FieldKhongCoThat=9` vẫn exit 0), nên đây là lưới an toàn duy nhất — test đối chiếu.
ASS_STYLE_FIELDS = [
    "FontName",
    "FontSize",
    "PrimaryColour",
    "OutlineColour",
    "BackColour",
    "Bold",
    "Italic",
    "Underline",
    "Spacing",
    "BorderStyle",
    "Outline",
    "Shadow",
    "Alignment",
    "MarginL",
    "MarginR",
    "MarginV",
]

_HEX_RE = re.compile(r"^#?([0-9a-f]{6})$", re.IGNORECASE)

_ENUMS = {
    "border": ("none", "outline", "box"),
    "position": ("top", "middle", "bottom"),
}

# Miền hợp lệ của field số — khớp clamp() trong migrateSubtitleStyle (subtitle-style.ts:206-234).
_RANGES = {
    "size_pct": (1, 20),
    "opacity": (0, 1),
    "outline_opacity": (0, 1),
    "letter_spacing": (-2, 10),
    "outline_width": (0, 4),
    "shadow_depth": (0, 4),
    "margin_pct": (0, 40),
    "side_margin_pct": (0, 25),
    "max_chars_per_line": (0, 80),
}


# JS `Number(str)` KHÁC `float(str)` của Python ở cả hai chiều: JS nhận `0x10`/`0b101`, Python
# thì không; Python nhận `1_0` và `inf`, JS thì không. Lệch là web và skill dựng chữ khác cỡ
# nhau từ cùng một config — nên chép đúng ngữ pháp số của JS thay vì mượn float().
_JS_DECIMAL_RE = re.compile(r"^[+-]?(Infinity|(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?)$")
_JS_RADIX_RE = re.compile(r"^0([xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)$")


def _to_number(v: object) -> float:
    """Mô phỏng Number(v) của JS cho mọi kiểu JSON sinh ra được. Không ép được → NaN."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):
        # JS ép mảng qua chuỗi: [] → "" → 0, [5] → "5" → 5, [1,2] → "1,2" → NaN.
        # [true] → "true" → NaN, nên bool trong mảng không được đi tiếp.
        if not v:
            return 0.0
        return _to_number(v[0]) if len(v) == 1 and not isinstance(v[0], bool) else math.nan
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0.0
        if _JS_RADIX_RE.match(s):
            return float(int(s[2:], {"x": 16, "o": 8, "b": 2}[s[1].lower()]))
        return float(s) if _JS_DECIMAL_RE.match(s) else math.nan
    return math.nan


def _clamp_silent(v: object, lo: float, hi: float, fallback: float) -> float:
    """clamp(v, lo, hi, fallback) của TS: sai kiểu → fallback, ngoài biên → kẹp về biên."""
    n = _to_number(v)
    return fallback if not math.isfinite(n) else min(hi, max(lo, n))


def ass_color(hex_color: str, opacity: float = 1.0) -> str:
    """#rrggbb + độ đục → &HAABBGGRR (ASS: AA=00 đục hoàn toàn, FF trong suốt hoàn toàn)."""
    # js_round chứ không round(): opacity=0.3 cho tích ĐÚNG 178.5, banker's rounding của
    # Python ra 178 còn Math.round ra 179 — cùng config mà web và skill khác màu.
    alpha = format(js_round((1 - _clamp_silent(opacity, 0, 1, 1)) * 255), "02x")
    m = _HEX_RE.match(str(hex_color).strip())
    if not m:
        return f"&H{alpha}FFFFFF".upper()
    rr, gg, bb = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
    return f"&H{alpha}{bb}{gg}{rr}".upper()


# ==== Chuẩn hoá + kẹp biên ====================================================
#
# Bản TS im lặng vì UI có slider — người dùng không gõ được số vô lý. CLI thì gõ tay, nuốt im
# lặng là dở: `--subs-style '{"size_pct":45}'` cho chữ nửa khung mà ffmpeg vẫn exit 0. Nên ở đây
# mỗi lần kẹp/bỏ giá trị là MỘT dòng stderr. Giá trị trả về vẫn giống hệt TS.


def _warn(message: str) -> None:
    print(f"[render] CẢNH BÁO: subs-style {message}", file=sys.stderr)


def _clamp(key: str, raw: object, fallback: float) -> float:
    """Như _clamp_silent nhưng nói ra khi phải can thiệp."""
    lo, hi = _RANGES[key]
    n = _to_number(raw)
    if not math.isfinite(n):
        _warn(f"{key}={raw!r} không phải số → dùng mặc định {fallback}")
        return fallback
    clamped = min(hi, max(lo, n))
    if clamped != n:
        _warn(f"{key}={raw} ngoài miền {lo}-{hi} → dùng {clamped:g}")
    return clamped


def _normalize_hex(key: str, raw: object, fallback: str) -> str:
    m = _HEX_RE.match(raw.strip()) if isinstance(raw, str) else None
    if not m:
        _warn(f"{key}={raw!r} không phải mã màu #rrggbb → dùng {fallback}")
        return fallback
    return f"#{m.group(1).lower()}"


def _enum(key: str, raw: object, fallback: str) -> str:
    allowed = _ENUMS[key]
    if raw in allowed:
        return str(raw)
    _warn(f"{key}={raw!r} không hợp lệ ({'|'.join(allowed)}) → dùng {fallback}")
    return fallback


def _bool(key: str, raw: object, fallback: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    _warn(f"{key}={raw!r} không phải true/false → dùng {str(fallback).lower()}")
    return fallback


def normalize_subtitle_style(raw: object) -> dict:
    """Style từ nguồn ngoài (CLI --subs-style, config cũ) → style hợp lệ, đủ 19 field.

    Bản Python của migrateSubtitleStyle() (lib/subtitle-style.ts:186-236): gộp cả quy đổi
    v1→v2 lẫn kẹp biên — config rác không được phép làm chết ffmpeg (hoặc tệ hơn: cho ra
    video sai mà exit 0).
    """
    d = SUBTITLE_STYLE_DEFAULTS
    if not isinstance(raw, dict):
        return dict(d)

    unknown = [k for k in raw if k not in d and k != "outline"]
    for k in unknown:
        _warn(f"field lạ {k!r} — bỏ qua (libass cũng bỏ qua im lặng field sai tên)")

    # v1 chỉ có `outline: bool`. Bật → viền dày 2 (xấp xỉ hình cũ), tắt → không viền.
    # Thứ tự nhánh bám đúng TS: `border` hợp lệ thắng; sai/thiếu thì mới hỏi tới legacy.
    legacy_outline = raw["outline"] if isinstance(raw.get("outline"), bool) else None
    if raw.get("border") in _ENUMS["border"]:
        border = raw["border"]
    else:
        border = d["border"] if legacy_outline is None else ("outline" if legacy_outline else "none")
        if "border" in raw:
            _warn(f"border={raw['border']!r} không hợp lệ (none|outline|box) → dùng {border}")

    if "outline_width" not in raw and legacy_outline is not None:
        outline_width = 2 if legacy_outline else 0
    else:
        outline_width = _clamp("outline_width", raw.get("outline_width", d["outline_width"]), d["outline_width"])

    max_chars = _clamp("max_chars_per_line", raw.get("max_chars_per_line", d["max_chars_per_line"]), d["max_chars_per_line"])
    # Wrap dưới 12 ký tự/dòng là vô nghĩa (chữ vỡ vụn) — coi như tắt.
    if 0 < max_chars < 12:
        _warn(f"max_chars_per_line={max_chars:g} dưới 12 (chữ vỡ vụn) → tắt wrap (0)")
        max_chars = 0

    font = raw.get("font", d["font"])
    if not (isinstance(font, str) and font.strip()):
        _warn(f"font={font!r} rỗng hoặc sai kiểu → dùng {d['font']}")
        font = d["font"]
    else:
        font = font.strip()

    def num(key: str) -> float:
        return _clamp(key, raw.get(key, d[key]), d[key])

    def flag(key: str) -> bool:
        return _bool(key, raw[key], d[key]) if key in raw else d[key]

    def hexc(key: str) -> str:
        return _normalize_hex(key, raw[key], d[key]) if key in raw else d[key]

    return {
        "font": font,
        "size_pct": num("size_pct"),
        "color": hexc("color"),
        "opacity": num("opacity"),
        "bold": flag("bold"),
        "italic": flag("italic"),
        "underline": flag("underline"),
        "uppercase": flag("uppercase"),
        "letter_spacing": num("letter_spacing"),
        "border": border,
        "outline_width": outline_width,
        "outline_color": hexc("outline_color"),
        "outline_opacity": num("outline_opacity"),
        "shadow_depth": num("shadow_depth"),
        "shadow_color": hexc("shadow_color"),
        "position": _enum("position", raw["position"], d["position"]) if "position" in raw else d["position"],
        "margin_pct": num("margin_pct"),
        "side_margin_pct": num("side_margin_pct"),
        "max_chars_per_line": js_round(max_chars),
    }


# ==== Preset + font (đọc từ snapshot parity) ==================================
#
# Nạp LƯỜI có chủ ý: `schemas/parity.json` là dữ liệu của preset và catalog font, không phải
# thứ đường render mặc định cần tới. Xoá nhầm file thì `--subs-preset` báo lỗi rõ ràng, chứ
# render thường vẫn chạy.

_parity_cache: dict | None = None


def load_parity() -> dict:
    """Đọc schemas/parity.json (cache theo process). Thiếu file → FileNotFoundError chỉ rõ cách sinh."""
    global _parity_cache
    if _parity_cache is None:
        if not PARITY_FILE.exists():
            raise FileNotFoundError(
                f"thiếu {PARITY_FILE} — sinh lại bằng `pnpm gen:parity` trong repo content-extractor"
            )
        _parity_cache = json.loads(PARITY_FILE.read_text(encoding="utf-8"))
    return _parity_cache


def subtitle_presets() -> dict[str, dict]:
    """{id: {label, hint, style}} — 5 preset dựng sẵn, khớp bản web từng field."""
    return {p["id"]: p for p in load_parity()["subtitle_presets"]}


def preset_ids() -> list[str]:
    """Id preset cho argparse choices. Thiếu snapshot → [] để CLI vẫn dựng được parser."""
    try:
        return list(subtitle_presets())
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return []


def resolve_subtitle_style(preset_id: str | None, override: dict | None = None) -> dict:
    """Hợp nhất DEFAULTS ◀ preset ◀ override rồi kẹp biên.

    Preset chỉ đụng phần TẠO HÌNH — `max_chars_per_line` (và `export`/`burn_in` bên web) cố ý
    không nằm trong preset vì thuộc quyết định của người dùng, không thuộc kiểu dáng.
    """
    base: dict = {}
    if preset_id:
        presets = subtitle_presets()
        if preset_id not in presets:
            raise KeyError(f"preset {preset_id!r} không có; hợp lệ: {', '.join(presets)}")
        base = dict(presets[preset_id]["style"])
    return normalize_subtitle_style({**base, **(override or {})})


def warn_font(style: dict) -> None:
    """Cảnh báo khi font có thể làm phụ đề mất dấu tiếng Việt. KHÔNG fail — dùng font ngoài
    catalog là quyền của người dùng, và libass rơi về font hệ thống trong im lặng nên đây là
    lời nhắc duy nhất họ nhận được.

    Hai mức khác hẳn nhau: nằm trong blacklist = đã đo là THIẾU dấu; chỉ là ngoài catalog =
    không biết, có thể hoàn toàn hợp lệ.
    """
    try:
        fonts = load_parity()["fonts"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return  # không có snapshot thì không có gì để đối chiếu — im lặng, đừng chặn render
    font = style.get("font", "")
    if font in fonts.get("blacklist", []):
        print(
            f"[render] CẢNH BÁO: font {font!r} THIẾU dấu tiếng Việt — phụ đề sẽ mất dấu hoặc ra ô "
            f"vuông. Dùng một trong: {', '.join(fonts.get('catalog', []))}",
            file=sys.stderr,
        )
    elif font not in fonts.get("catalog", []):
        print(
            f"[render] font {font!r} ngoài catalog — chưa kiểm dấu tiếng Việt. Nếu phụ đề mất dấu, "
            f"đổi sang: {', '.join(fonts.get('catalog', []))}",
            file=sys.stderr,
        )


# ==== Quy đổi sang ASS ========================================================


def _safe_font_name(font: str) -> str:
    """Lọc ký tự phá cú pháp force_style (' : , \\) — font là free-text từ config cũ.

    Lớp ký tự viết tường minh chứ không dùng `\\w`: `\\w` của Python là Unicode nên giữ lại
    CJK/kana/Hangul, còn `\\w` của JS chỉ là ASCII nên bản TS xoá sạch chúng. Dải `À-ỹ` dừng ở
    U+1EF9, đủ tiếng Việt và không chạm CJK.
    """
    return re.sub(r"[^A-Za-z0-9_ À-ỹ.-]", "", font).strip() or "Arial"


def _num(v: float) -> str:
    """In số như JS nội suy vào template: 3.0 → "3", 0.5 → "0.5"."""
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


def to_ass_force_style(style: dict) -> str:
    """Chuỗi force_style cho filter `subtitles` của ffmpeg (toAssForceStyle của TS).

    Vai trò màu trong ASS (ĐÃ ĐO bằng libass 0.17, KHÔNG suy đoán):

                     BorderStyle=1 (viền)   BorderStyle=3 (hộp)
        OutlineColour  màu viền              MÀU HỘP
        BackColour     màu bóng              màu bóng

    Thứ tự field phải khớp ASS_STYLE_FIELDS — thuộc hợp đồng, test parity so chuỗi tuyệt đối.
    """
    font_size = max(8, js_round((style["size_pct"] / 100) * FONT_SIZE_DIVISOR))
    margin_v = js_round((style["margin_pct"] / 100) * PLAY_RES_Y)
    margin_side = js_round((style["side_margin_pct"] / 100) * PLAY_RES_X)
    outline = 0 if style["border"] == "none" else style["outline_width"]
    return ",".join([
        f"FontName={_safe_font_name(style['font'])}",
        f"FontSize={font_size}",
        f"PrimaryColour={ass_color(style['color'], style['opacity'])}",
        f"OutlineColour={ass_color(style['outline_color'], style['outline_opacity'])}",
        f"BackColour={ass_color(style['shadow_color'], 1.0)}",
        f"Bold={1 if style['bold'] else 0}",
        f"Italic={1 if style['italic'] else 0}",
        f"Underline={1 if style['underline'] else 0}",
        f"Spacing={_num(style['letter_spacing'])}",
        f"BorderStyle={3 if style['border'] == 'box' else 1}",
        f"Outline={_num(outline)}",
        f"Shadow={_num(style['shadow_depth'])}",
        f"Alignment={_ALIGNMENT.get(style['position'], 2)}",
        f"MarginL={margin_side}",
        f"MarginR={margin_side}",
        f"MarginV={margin_v}",
    ])
