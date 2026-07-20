# Port từ content-extractor/lib/schema.ts (SceneContent :4-8, estimateSeconds :127-131)
# — đồng bộ 2 nơi khi fix bug.
# Hằng số + helper dùng chung cho hợp đồng workdir của skill manga-to-video.

from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1
SCRIPT_VERSION = 1

# ---- Đường dẫn chuẩn trong workdir (hợp đồng Phase 2 — các phase sau chỉ THÊM field) ----


def manifest_path(workdir: Path) -> Path:
    return Path(workdir) / "manifest.json"


def slices_dir(workdir: Path) -> Path:
    return Path(workdir) / "slices"


def content_dir(workdir: Path) -> Path:
    return Path(workdir) / "content"


def content_path(workdir: Path, slice_id: str) -> Path:
    return content_dir(workdir) / f"{slice_id}.json"


def script_path(workdir: Path) -> Path:
    return Path(workdir) / "script.json"


def audio_dir(workdir: Path) -> Path:
    return Path(workdir) / "audio"


def audio_manifest_path(workdir: Path) -> Path:
    return Path(workdir) / "audio-manifest.json"


def out_dir(workdir: Path) -> Path:
    return Path(workdir) / "out"


# ---- IO atomic (.tmp + rename — tránh manifest nát khi kill giữa chừng) ----


def save_json_atomic(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Hậu tố pid+uuid: hai writer cùng key chạy song song không giẫm file tmp của nhau
    # (mẫu writeAtomic tts.ts:115-120)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_manifest(workdir: Path) -> dict:
    return load_json(manifest_path(workdir))


def save_manifest(workdir: Path, manifest: dict) -> None:
    save_json_atomic(manifest_path(workdir), manifest)


# ---- Ước lượng thời lượng đọc (port estimateSeconds, schema.ts:127-131) ----
# TTS tiếng Việt đọc ~15 ký tự/giây; narration rỗng → 0.


def estimate_seconds(narration: str) -> int:
    chars = len(narration.strip())
    if chars == 0:
        return 0
    return max(1, math.ceil(chars / 15))
