# Port từ content-extractor/lib/kenburns.ts — đồng bộ 2 nơi khi fix bug.
# Ken Burns per shot — module THUẦN trả chuỗi filtergraph ffmpeg (không spawn, dễ test).
# Biểu thức filter trích NGUYÊN VĂN từ TS — parity gate: chuỗi Python phải byte-identical
# với TS cùng input (harness tools/parity-port/compare_filters).
#
# Chống shake: crop/zoompan lượng tử hoá toạ độ integer mỗi frame → mọi nhánh có chuyển
# động dựng ở SUPERSAMPLE× rồi downscale; riêng zoom thêm tmix 2 frame. Pan KHÔNG tmix
# (motion ~9px/frame sẽ thành bóng ma).

from __future__ import annotations

from .js_math import js_round

STRIP_SHAPE = 1.6
PAN_MIN_COVER = 1.05
# Trần phần dư phụ thuộc hình dáng KHUNG (kenburns.ts:52-64): coverV = tallness ×
# frameRatio nên cùng con số cho ra hai dải tallness khác hẳn. Khung ngang phóng ảnh 720px
# lên 1920 (2.67× thay vì 1.5×) — trần 3 loại gần sạch pan, trần 6 cho 89/116 slice lia.
PAN_MAX_COVER = 3
PAN_MAX_COVER_WIDE = 6
MAX_PAN_FRAME_PER_SEC = 0.35
SUPERSAMPLE = 4
# Hạ supersample nhánh pan khi khung trung gian quá lớn (kenburns.ts:66-77). Trần nằm trên
# mức tối đa cấu trúc của khung dọc/vuông (24.9 MP) nên hai khung đó không bao giờ chạm.
PAN_SUPERSAMPLE_MAX_PX = 26_000_000
PAN_SUPERSAMPLE_LOW = 2


def _pan_max_cover(frame_ratio: float) -> int:
    return PAN_MAX_COVER_WIDE if frame_ratio > 1 else PAN_MAX_COVER


def _pan_supersample(cross_axis_px: int, scaled_px: int) -> int:
    over = cross_axis_px * scaled_px * SUPERSAMPLE > PAN_SUPERSAMPLE_MAX_PX
    return PAN_SUPERSAMPLE_LOW if over else SUPERSAMPLE

FRAME_SIZES = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


def default_fill(aspect: str) -> str:
    """9:16 lấp kín ('cover'), khung ngang/vuông nền blur — kenburns.ts defaultFill."""
    return "cover" if aspect == "9:16" else "blur"


def resolve_shot_motion(motion: dict | None, default_motion: str) -> dict | None:
    """Ghi đè per-shot luôn thắng; shot auto nhận hiệu ứng mặc định (kenburns.ts resolveShotMotion)."""
    if motion is not None and motion.get("mode") != "auto":
        return motion
    return {"mode": "static"} if default_motion == "static" else None


def frame_size(aspect: str) -> tuple[int, int]:
    if aspect not in FRAME_SIZES:
        raise ValueError(f"Aspect không hỗ trợ: {aspect} (9:16|16:9|1:1)")
    return FRAME_SIZES[aspect]


def even(n: float) -> int:
    # mọi kích thước làm tròn về chẵn (yuv420p)
    return max(2, js_round(n / 2) * 2)


def _pan_v_plan(r: dict, W: int, H: int, duration_sec: float, reverse: bool = False) -> dict:
    scaled_h = even((r["h"] * W) / r["w"])
    travel_full = scaled_h - H
    travel = js_round(min(travel_full, MAX_PAN_FRAME_PER_SEC * H * duration_sec))
    return {"mode": "pan-v", "blur_fill": False, "travel_px": travel,
            "base_px": js_round((travel_full - travel) / 2), "reverse": reverse}


def _pan_h_plan(r: dict, W: int, H: int, duration_sec: float, reverse: bool = False) -> dict:
    scaled_w = even((r["w"] * H) / r["h"])
    travel_full = scaled_w - W
    travel = js_round(min(travel_full, MAX_PAN_FRAME_PER_SEC * W * duration_sec))
    return {"mode": "pan-h", "blur_fill": False, "travel_px": travel,
            "base_px": js_round((travel_full - travel) / 2), "reverse": reverse}


def plan_kenburns(input: dict) -> dict:
    """input: {img_w, img_h, region|None, aspect, duration_sec, fps, zoom, motion|None, fill?}.

    fill: 'blur' (mặc định, hành vi cũ) | 'cover' (lấp kín khung, không nền blur).
    """
    W, H = frame_size(input["aspect"])
    r = input.get("region") or {"x": 0, "y": 0, "w": input["img_w"], "h": input["img_h"]}
    tallness = r["h"] / r["w"]
    wideness = r["w"] / r["h"]
    frame_ratio = W / H
    cover_v = tallness * frame_ratio
    cover_h = wideness / frame_ratio
    aspect_mismatch = abs(wideness - frame_ratio) / frame_ratio > 0.02
    # fill 'cover': lấp kín khung + crop giữa thay cho nền blur. cover_fill chỉ có mặt khi
    # True để plan của fill 'blur' giữ nguyên hình dạng cũ.
    cover = input.get("fill") == "cover"
    fill_plan = {"blur_fill": aspect_mismatch and not cover}
    if aspect_mismatch and cover:
        fill_plan["cover_fill"] = True

    # ==== Ghi đè per-shot (mode ≠ auto) — kenburns.ts:115-140 ====
    motion = input.get("motion")
    forced = motion if motion is not None and motion.get("mode") != "auto" else None
    if forced:
        reverse = forced.get("reverse") is True
        if forced["mode"] == "pan-v" and cover_v >= PAN_MIN_COVER:
            return _pan_v_plan(r, W, H, input["duration_sec"], reverse)
        if forced["mode"] == "pan-h" and cover_h >= PAN_MIN_COVER:
            return _pan_h_plan(r, W, H, input["duration_sec"], reverse)
        if forced["mode"] == "zoom":
            return {"mode": "zoom", **fill_plan, "reverse": reverse}
        if forced["mode"] == "static":
            return {"mode": "static", **fill_plan, "reverse": False}
        # Ép pan nhưng vùng không đủ phần dư → rơi về auto phía dưới (an toàn)

    # ==== auto: suy từ hình dáng region — kenburns.ts:150-160 ====
    max_cover = _pan_max_cover(frame_ratio)
    if tallness >= STRIP_SHAPE and PAN_MIN_COVER <= cover_v <= max_cover:
        return _pan_v_plan(r, W, H, input["duration_sec"])
    if wideness >= STRIP_SHAPE and PAN_MIN_COVER <= cover_h <= max_cover:
        return _pan_h_plan(r, W, H, input["duration_sec"])
    return {"mode": "zoom", **fill_plan, "reverse": False}


def kenburns(input: dict) -> str:
    """Filtergraph cho render_shot — luôn kết thúc nhãn [v] (kenburns.ts:175-257)."""
    W, H = frame_size(input["aspect"])
    r = input.get("region") or {"x": 0, "y": 0, "w": input["img_w"], "h": input["img_h"]}
    plan = plan_kenburns(input)
    dur = max(0.1, input["duration_sec"])
    crop_reg = f"crop={r['w']}:{r['h']}:{r['x']}:{r['y']}"
    # Tiến độ pan theo t: reverse chạy 1→0
    frac = f"(1-t/{dur:.3f})" if plan.get("reverse") else f"(t/{dur:.3f})"

    S = SUPERSAMPLE

    def fit_to(w: int, h: int) -> str:
        # cover phóng phủ kín rồi crop giữa; còn lại scale thẳng (lệch ≤2%)
        if plan.get("cover_fill"):
            return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        return f"scale={w}:{h}"

    if plan["mode"] == "pan-v":
        scaled = even((r["h"] * W) / r["w"])
        sp = _pan_supersample(W, scaled)
        travel = plan.get("travel_px") or 0
        base = plan.get("base_px") or 0
        y = f"'{base * sp}+{travel * sp}*{frac}'" if travel > 0 else str(base * sp)
        return f"[0:v]{crop_reg},scale={W}:{scaled * sp},crop={W}:{H * sp}:0:{y},scale={W}:{H},setsar=1[v]"

    if plan["mode"] == "pan-h":
        scaled = even((r["w"] * H) / r["h"])
        sp = _pan_supersample(H, scaled)
        travel = plan.get("travel_px") or 0
        base = plan.get("base_px") or 0
        x = f"'{base * sp}+{travel * sp}*{frac}'" if travel > 0 else str(base * sp)
        return f"[0:v]{crop_reg},scale={scaled * sp}:{H},crop={W * sp}:{H}:{x}:0,scale={W}:{H},setsar=1[v]"

    if plan["mode"] == "static":
        # static BỎ HẲN zoompan (z=1 qua toFixed vẫn có thể thành micro-zoom)
        if not plan["blur_fill"]:
            return f"[0:v]{crop_reg},{fit_to(W, H)},setsar=1[v]"
        fit_scale = min(W / r["w"], H / r["h"])
        fw = even(r["w"] * fit_scale)
        fh = even(r["h"] * fit_scale)
        return (
            f"[0:v]split=2[bg][fg];"
            f"[bg]{crop_reg},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:1[bgb];"
            f"[fg]{crop_reg},scale={fw}:{fh}[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )

    # mode zoom: supersample trước zoompan + tmix 2 frame sau (kenburns.ts:227-256)
    frames = max(2, js_round(dur * input["fps"]))
    zoom_target = max(1, input["zoom"])
    # reverse dạng đóng Z-(Z-1)*on/(frames-1) — CẤM idiom tự tham chiếu frame trước
    if plan.get("reverse"):
        z_expr = f"'{zoom_target:.4f}-{zoom_target - 1:.4f}*on/{frames - 1}'"
    else:
        z_expr = f"'1+{zoom_target - 1:.4f}*on/{frames - 1}'"
    zoom_center = "x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"

    if not plan["blur_fill"]:
        return (
            f"[0:v]{crop_reg},{fit_to(W * S, H * S)},"
            f"zoompan=z={z_expr}:{zoom_center}:d=1:s={W}x{H},tmix=frames=2,setsar=1[v]"
        )

    fit_scale = min(W / r["w"], H / r["h"])
    fw = even(r["w"] * fit_scale)
    fh = even(r["h"] * fit_scale)
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]{crop_reg},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:1[bgb];"
        f"[fg]{crop_reg},scale={fw * S}:{fh * S},"
        f"zoompan=z={z_expr}:{zoom_center}:d=1:s={fw}x{fh},tmix=frames=2[fgz];"
        f"[bgb][fgz]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )
