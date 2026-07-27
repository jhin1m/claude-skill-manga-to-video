#!/usr/bin/env python3
"""Đối chiếu engine Python với snapshot schemas/parity.json do repo TS xuất ra.

Skill này là port TAY của content-extractor/lib — 45+ hằng số và toàn bộ logic style phụ đề
tồn tại hai bản. Trước khi có test này, drift chỉ lộ ra bằng video sai: smoke test cũ chỉ kiểm
schema/fixture, không so một con số nào với TS.

TS là NGUỒN CHÂN LÝ. Test đỏ nghĩa là sửa Python cho khớp snapshot, KHÔNG sửa snapshot cho
khớp Python (snapshot chỉ được sinh lại bằng `pnpm gen:parity` bên repo TS).

Chạy: ~/.claude/skills/.venv/bin/python3 tests/test_parity.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mtv.auto_region import AUTO_REGION_DEFAULTS
from mtv.kenburns import (
    MAX_PAN_FRAME_PER_SEC,
    PAN_MAX_COVER,
    PAN_MAX_COVER_WIDE,
    PAN_MIN_COVER,
    PAN_SUPERSAMPLE_LOW,
    PAN_SUPERSAMPLE_MAX_PX,
    STRIP_SHAPE,
    SUPERSAMPLE,
)
from mtv.shot_split import MIN_BAND_RATIO, SHOT_MAX_HEIGHT_DEFAULT
from mtv.subtitle_style import (
    ASS_STYLE_FIELDS,
    FONT_SIZE_DIVISOR,
    PARITY_FILE,
    PLAY_RES_X,
    PLAY_RES_Y,
    SUBTITLE_STYLE_DEFAULTS,
    _ALIGNMENT,
    normalize_subtitle_style,
    resolve_subtitle_style,
    subtitle_presets,
    to_ass_force_style,
)
from mtv.timeline import TIMELINE_DEFAULTS, format_caption

SUPPORTED_VERSION = 1

failures: list[str] = []


def eq(actual, expected, label: str) -> None:
    """So bằng `==` chứ không dung sai: lệch thật thì phải đỏ, kể cả 0.35 vs 0.3500001."""
    if actual == expected:
        print(f"  ✓ {label}")
    else:
        failures.append(label)
        print(f"  ✗ {label}\n      snapshot (đúng): {expected!r}\n      Python:          {actual!r}")


def main() -> None:
    if not PARITY_FILE.exists():
        print(f"THIẾU {PARITY_FILE} — chạy `pnpm gen:parity` bên repo content-extractor")
        sys.exit(1)
    snap = json.loads(PARITY_FILE.read_text(encoding="utf-8"))
    if snap.get("version") != SUPPORTED_VERSION:
        print(f"parity.json version={snap.get('version')}, test này chỉ đọc được version {SUPPORTED_VERSION}")
        sys.exit(1)

    c = snap["constants"]

    print("test_parity: hằng auto_region")
    for key, expected in c["auto_region"].items():
        eq(AUTO_REGION_DEFAULTS.get(key), expected, f"AUTO_REGION_DEFAULTS[{key!r}]")
    eq(sorted(AUTO_REGION_DEFAULTS), sorted(c["auto_region"]), "AUTO_REGION_DEFAULTS đủ khoá, không thừa")

    print("test_parity: hằng kenburns")
    kb = {
        "strip_shape": STRIP_SHAPE,
        "pan_min_cover": PAN_MIN_COVER,
        "pan_max_cover": PAN_MAX_COVER,
        "pan_max_cover_wide": PAN_MAX_COVER_WIDE,
        "max_pan_frame_per_sec": MAX_PAN_FRAME_PER_SEC,
        "supersample": SUPERSAMPLE,
        "pan_supersample_max_px": PAN_SUPERSAMPLE_MAX_PX,
        "pan_supersample_low": PAN_SUPERSAMPLE_LOW,
    }
    for key, expected in c["kenburns"].items():
        eq(kb.get(key), expected, f"kenburns.{key}")

    print("test_parity: hằng timeline")
    for key, expected in c["timeline"].items():
        eq(TIMELINE_DEFAULTS.get(key), expected, f"TIMELINE_DEFAULTS[{key!r}]")

    print("test_parity: hằng shot_split")
    eq(SHOT_MAX_HEIGHT_DEFAULT, c["shot_split"]["shot_max_height_default"], "SHOT_MAX_HEIGHT_DEFAULT")
    eq(MIN_BAND_RATIO, c["shot_split"]["min_band_ratio"], "MIN_BAND_RATIO")

    print("test_parity: hằng subtitle_style")
    ss = c["subtitle_style"]
    eq(PLAY_RES_X, ss["play_res_x"], "PLAY_RES_X")
    eq(PLAY_RES_Y, ss["play_res_y"], "PLAY_RES_Y")
    eq(FONT_SIZE_DIVISOR, ss["font_size_divisor"], "FONT_SIZE_DIVISOR")
    eq(_ALIGNMENT, ss["alignment"], "_ALIGNMENT (hệ SSA v4 cũ, không phải numpad)")
    # libass bỏ qua im lặng field sai tên → thứ tự + chính tả là lưới an toàn duy nhất.
    eq(ASS_STYLE_FIELDS, ss["ass_style_fields"], "ASS_STYLE_FIELDS (đúng thứ tự)")

    print("test_parity: SUBTITLE_STYLE_DEFAULTS")
    for key, expected in snap["subtitle_defaults"].items():
        eq(SUBTITLE_STYLE_DEFAULTS.get(key), expected, f"SUBTITLE_STYLE_DEFAULTS[{key!r}]")
    eq(sorted(SUBTITLE_STYLE_DEFAULTS), sorted(snap["subtitle_defaults"]), "SUBTITLE_STYLE_DEFAULTS đủ 19 field")

    print("test_parity: golden force_style (chuỗi so tuyệt đối, kể cả thứ tự field)")
    for case in snap["golden"]["force_style"]:
        eq(to_ass_force_style(case["style"]), case["expected"], f"to_ass_force_style({case['name']})")

    print("test_parity: golden normalize (clamp + migrate v1→v2)")
    for case in snap["golden"]["normalize"]:
        eq(normalize_subtitle_style(case["raw"]), case["expected"], f"normalize({case['name']})")

    # Vòng khép kín của --subs-preset: đi qua đúng đường CLI dùng (resolve → force_style) và
    # phải trùng chuỗi golden mà TS xuất cho preset đó. Chọn "punchy" trên web rồi render lại
    # bằng skill ra khác nhau là hỏng đúng chỗ này.
    print("test_parity: --subs-preset ra đúng golden của web")
    golden_by_name = {g["name"]: g["expected"] for g in snap["golden"]["force_style"]}
    for pid in subtitle_presets():
        eq(
            to_ass_force_style(resolve_subtitle_style(pid)),
            golden_by_name.get(f"preset:{pid}"),
            f"resolve_subtitle_style({pid!r}) → force_style",
        )
    # Override phải thắng preset, phần còn lại của preset giữ nguyên.
    punchy = resolve_subtitle_style("punchy", {"size_pct": 5})
    eq(punchy["size_pct"], 5, "override đè preset (size_pct)")
    eq(
        [punchy["font"], punchy["bold"], punchy["uppercase"], punchy["outline_width"], punchy["margin_pct"]],
        ["Kanit", True, True, 3, 12],
        "phần còn lại vẫn của punchy",
    )
    # Preset không đụng tới max_chars_per_line (quyết định của người dùng, không phải tạo hình).
    eq(punchy["max_chars_per_line"], SUBTITLE_STYLE_DEFAULTS["max_chars_per_line"], "preset không suy ra max_chars_per_line")
    # boxed/news thiếu shadow_color trong định nghĩa → phải rơi về default, không phải lỗi.
    eq(resolve_subtitle_style("boxed")["shadow_color"], "#000000", "boxed thiếu shadow_color → default")

    print("test_parity: golden captions (uppercase + ngắt dòng)")
    for case in snap["golden"]["captions"]:
        eq(format_caption(case["text"], case["style"]), case["expected"], f"format_caption({case['name']})")

    print(f"\ntest_parity: {'PASS' if not failures else f'FAIL {len(failures)} case'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
