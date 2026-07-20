#!/usr/bin/env python3
# Port từ content-extractor/lib/tts.ts (cache key :50-61, synthSegment :73-110) — đồng bộ
# 2 nơi khi fix bug. KHÁC TS: default provider là **capcut** (TS default elevenlabs) —
# quyết định skill-level vì CapCut không cần API key.
#
# CLI: tts.py --workdir W [--provider capcut|elevenlabs] [--voice ID] [--model M] [--force]
# Đọc script.json (validate trước, fail thì dừng) → synth từng segment với cache đĩa
# audio/<sha256>.mp3 + .json meta → ghi audio-manifest.json + manifest.stages.tts.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtv import tts_capcut, tts_elevenlabs
from mtv.ffmpeg_cmd import probe_duration_sec
from mtv.schema_defs import (
    audio_dir,
    audio_manifest_path,
    load_json,
    load_manifest,
    save_json_atomic,
    save_manifest,
    script_path,
)

DEFAULT_PROVIDER = "capcut"  # skill-level default — TS default là elevenlabs

PROVIDERS = {
    "capcut": tts_capcut,
    "elevenlabs": tts_elevenlabs,
}


def fail(rule: str, message: str) -> None:
    print(json.dumps({"file": "tts", "path": "$", "rule": rule, "message": message}, ensure_ascii=False))
    sys.exit(1)


def tts_cache_key(text: str, voice_id: str, model: str, provider: str) -> str:
    """Port ttsCacheKey (tts.ts:50-61): elevenlabs giữ công thức legacy KHÔNG prefix."""
    if provider == "elevenlabs":
        material = f"{text}\x00{voice_id}\x00{model}"
    else:
        material = f"{provider}\x00{text}\x00{voice_id}\x00{model}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def write_atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def synth_segment(text: str, cfg: dict, adir: Path, force: bool) -> dict:
    """Synth MỘT đoạn → {key, duration_sec, file, cached}. Text rỗng → duration 0, không synth."""
    provider_mod = PROVIDERS[cfg["provider"]]
    trimmed = text.strip()
    voice_for_key = provider_mod.resolve_voice_id(cfg)
    key = tts_cache_key(trimmed, voice_for_key, cfg["model"], cfg["provider"])
    if trimmed == "":
        return {"key": key, "duration_sec": 0.0, "file": None, "cached": True}

    adir.mkdir(parents=True, exist_ok=True)
    mp3_path = adir / f"{key}.mp3"
    meta_path = adir / f"{key}.json"

    if mp3_path.exists() and not force:
        duration = None
        try:
            meta = load_json(meta_path)
            if isinstance(meta.get("duration_sec"), (int, float)):
                duration = float(meta["duration_sec"])
        except (OSError, json.JSONDecodeError):
            pass  # metadata hỏng/thiếu → đo lại
        if duration is None:
            duration = probe_duration_sec(str(mp3_path))
            save_json_atomic(meta_path, {"duration_sec": duration})
        return {"key": key, "duration_sec": duration, "file": mp3_path.name, "cached": True}

    audio = provider_mod.synth(trimmed, cfg)
    write_atomic_bytes(mp3_path, audio)
    duration = probe_duration_sec(str(mp3_path))
    save_json_atomic(meta_path, {"duration_sec": duration})
    return {"key": key, "duration_sec": duration, "file": mp3_path.name, "cached": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS từng segment của script.json với cache đĩa")
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--provider", choices=list(PROVIDERS))
    parser.add_argument("--voice", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--force", action="store_true", help="bỏ qua cache, synth lại tất cả")
    args = parser.parse_args()

    # Resolution: flag > env TTS_PROVIDER > default capcut
    provider = args.provider or os.environ.get("TTS_PROVIDER", "").strip() or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        fail("provider-unknown", f"Provider '{provider}' không hỗ trợ (capcut|elevenlabs)")

    # validate script trước — fail thì dừng (đầu ra validate đã là JSON lines)
    validate = Path(__file__).resolve().parent / "validate.py"
    proc = subprocess.run(
        [sys.executable, str(validate), "--workdir", str(args.workdir), "--stage", "script"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.exit(1)

    model = args.model or (tts_elevenlabs.resolve_model({}) if provider == "elevenlabs" else "")
    cfg = {"provider": provider, "voice_id": args.voice, "model": model}

    script = load_json(script_path(args.workdir))
    adir = audio_dir(args.workdir)
    segments_out: dict[str, dict] = {}
    hits = 0
    try:
        for seg in script["segments"]:
            result = synth_segment(seg["narration"], cfg, adir, args.force)
            hits += 1 if result.pop("cached") else 0
            segments_out[seg["id"]] = result
    except Exception as err:
        fail("synth-error", str(err))

    voice_id = PROVIDERS[provider].resolve_voice_id(cfg)
    save_json_atomic(
        audio_manifest_path(args.workdir),
        {"provider": provider, "voice_id": voice_id, "model": model, "segments": segments_out},
    )
    manifest = load_manifest(args.workdir)
    manifest["stages"]["tts"] = "done"
    save_manifest(args.workdir, manifest)

    total = sum(s["duration_sec"] for s in segments_out.values())
    print(f"OK tts: {len(segments_out)} segment ({hits} cache hit) | provider {provider}, "
          f"voice {voice_id} | tổng audio ~{total:.1f}s")


if __name__ == "__main__":
    main()
