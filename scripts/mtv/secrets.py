# Secrets của skill manga-to-video: env thắng, fallback ~/.claude/skills/manga-to-video/.secrets.json
# (map phẳng {"ELEVENLABS_API_KEY": "..."}). KHÔNG bao giờ in giá trị ra log.

from __future__ import annotations

import json
import os
from pathlib import Path

SECRETS_PATH = Path(__file__).resolve().parent.parent.parent / ".secrets.json"

_file_cache: dict | None = None


def get_secret(name: str, default: str = "") -> str:
    global _file_cache
    value = os.environ.get(name)
    if value:
        return value
    if _file_cache is None:
        try:
            _file_cache = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _file_cache = {}
    return str(_file_cache.get(name, default) or default)
