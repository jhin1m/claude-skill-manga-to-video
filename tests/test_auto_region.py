#!/usr/bin/env python3
"""Smoke test auto_region v2.1 — full-width vertical window."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from mtv.auto_region import auto_region, auto_region_within


def make_scene(w: int, h: int, art, bubble) -> np.ndarray:
    rgb = np.full((h, w, 3), 160, dtype=np.uint8)
    ax, ay, aw, ah = art
    for y in range(ay, ay + ah):
        for x in range(ax, ax + aw):
            v = 40 if (x + y) % 6 < 3 else 90
            rgb[y, x] = (v, v, v)
    bx, by, bw, bh = bubble
    rgb[by : by + bh, bx : bx + bw] = 250
    for y in range(by + 8, by + bh - 8, 6):
        for x in range(bx + 8, bx + bw - 8, 5):
            rgb[y, x] = (20, 20, 20)
    return rgb


def main() -> None:
    # Bubble dải trên → cửa sổ dọc lệch xuống, FULL width
    img = make_scene(400, 700, (20, 280, 360, 400), (80, 10, 240, 180))
    r = auto_region(img)
    assert r is not None, "expect vertical crop"
    assert r["x"] == 0 and r["w"] == 400, f"must full width: {r}"
    assert r["y"] > 0 or r["h"] < 700, r
    assert r["h"] >= 0.75 * 700, r

    pure = np.zeros((600, 400, 3), dtype=np.uint8)
    for y in range(600):
        for x in range(400):
            v = 50 if (x + y) % 6 < 3 else 100
            pure[y, x] = (v, v, v)
    assert auto_region(pure) is None

    img2 = make_scene(400, 800, (50, 450, 300, 300), (80, 20, 240, 160))
    base = {"x": 0, "y": 200, "w": 400, "h": 600}
    r2 = auto_region_within(img2, base)
    assert r2 is not None
    assert r2["w"] == 400 or r2["w"] >= 350
    assert r2["y"] >= base["y"] - 1
    assert r2["y"] + r2["h"] <= base["y"] + base["h"] + 1

    print("test_auto_region: PASS")


if __name__ == "__main__":
    main()
