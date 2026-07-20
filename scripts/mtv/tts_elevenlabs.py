# Port từ content-extractor/lib/tts-providers/elevenlabs.ts — đồng bộ 2 nơi khi fix bug.
# POST /v1/text-to-speech/{voice_id}, headers xi-api-key, timeout 120s, retry 429/5xx/mạng.

from __future__ import annotations

import requests

from .http_retry import HttpStatusError, with_retry
from .secrets import get_secret

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL = "eleven_multilingual_v2"
REQUEST_TIMEOUT_S = 120


def resolve_voice_id(cfg: dict) -> str:
    return cfg.get("voice_id") or get_secret("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID


def resolve_model(cfg: dict) -> str:
    return cfg.get("model") or get_secret("ELEVENLABS_MODEL") or DEFAULT_MODEL


def synth(text: str, cfg: dict) -> bytes:
    api_key = get_secret("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Thiếu ELEVENLABS_API_KEY — khai báo qua env hoặc ~/.claude/skills/manga-to-video/.secrets.json"
        )
    base_url = (get_secret("ELEVENLABS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    voice_id = resolve_voice_id(cfg)
    model = resolve_model(cfg)

    def fetch() -> bytes:
        res = requests.post(
            f"{base_url}/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": api_key, "content-type": "application/json", "accept": "audio/mpeg"},
            json={"text": text, "model_id": model},
            timeout=REQUEST_TIMEOUT_S,
        )
        if not res.ok:
            raise HttpStatusError(f"ElevenLabs HTTP {res.status_code}", status=res.status_code)
        return res.content

    return with_retry(fetch)
