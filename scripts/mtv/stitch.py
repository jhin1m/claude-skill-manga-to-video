# Port từ content-extractor/lib/stitch.ts — đồng bộ 2 nơi khi fix bug.
# Gộp các slice dải dọc thành MỘT canvas dọc trắng: max-width, mỗi slice căn giữa ngang,
# stack dọc (mẫu SmartStitch).

from __future__ import annotations

from PIL import Image


def stitch_slices(slices: list[Image.Image]) -> Image.Image:
    """PIL Images → canvas RGB trắng (width = max, height = tổng), slice căn giữa ngang."""
    if not slices:
        raise ValueError("Không có slice nào để ghép")
    width = max(im.width for im in slices)
    height = sum(im.height for im in slices)

    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    top = 0
    for im in slices:
        left = (width - im.width) // 2
        if im.mode == "RGBA":
            canvas.paste(im, (left, top), im)  # alpha blend trên nền trắng (khớp sharp composite)
        else:
            canvas.paste(im.convert("RGB") if im.mode != "RGB" else im, (left, top))
        top += im.height
    return canvas
