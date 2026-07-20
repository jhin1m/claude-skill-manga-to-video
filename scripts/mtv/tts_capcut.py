# Port từ content-extractor/lib/tts-providers/capcut.ts — đồng bộ 2 nơi khi fix bug.
# CapCut TTS (API nội bộ reverse-engineered, free, giọng Việt): new task → poll → download.
# Signing: MD5 + RSA PKCS#1 v1.5 (package cryptography). API unofficial — có thể ngừng
# hoạt động; lỗi luôn kèm gợi ý đổi provider.
#
# Device id chống shark block (capcut.ts:313-370): env CAPCUT_DEVICE_JSON > store persist
# > generate random (snowflake 19 digit). Store của skill: <skill>/.cache/capcut-device.json,
# seed 1 lần từ content-extractor/.cache/capcut-device.json nếu tồn tại (identity đã thoát
# shark block — đừng đốt). Memoize per-process.

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as pysecrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .http_retry import HttpStatusError, with_retry
from .tts_catalog import CAPCUT_VOICES

DEFAULT_BASE_URL = "https://editor-api-sg.capcutapi.com"
DEFAULT_VOICE = "BV074_streaming"
DEFAULT_RESOURCE_ID = "7102355709945188865"
DEFAULT_POLL_INTERVAL_MS = 1500
DEFAULT_TOTAL_TIMEOUT_MS = 120_000
REQUEST_TIMEOUT_S = 30

SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
DEVICE_STORE_PATH = SKILL_ROOT / ".cache" / "capcut-device.json"
# Seed 1 lần từ app TS (cùng máy dùng chung identity ổn định)
SEED_DEVICE_PATH = Path(
    "~/Desktop/ducanh-project/manga-to-video/content-extractor/.cache/capcut-device.json"
).expanduser()

HINT = "Đổi --provider elevenlabs, hoặc xoá .cache/capcut-device.json để random identity mới (ret=-6 = shark block)."

# Device profile mặc định (capcut.ts:34-52) — BASE cho field app-level; 3 field định danh
# device_id/tdid/iid được thay bằng identity persist.
DEFAULT_DEVICE = {
    "aid": "359289",
    "app_name": "CapCut",
    "appvr": "8.7.0",
    "version_name": "8.7.0",
    "version_code": "8.7.0",
    "channel": "capcutpc_google",
    "device_platform": "mac",
    "device_type": "MacBookPro17,1",
    "device_brand": "MacBookPro17,1",
    "os_version": "15.7.4",
    "device_id": "7647183892936328721",
    "iid": "7647185302080423697",
    "region": "VN",
    "loc": "VN",
    "lan": "vi-VN",
    "pf": "3",
    "tdid": "7647183892936328721",
}

TTS_SIGN_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmTd34Lw4b7IuldSXh/zY
CMla+ITdGG5TeWz6ad+OySd4r+IrY45AoqrYUxhQ2dl+7z+i7r/5vEa8rr39BYfB
8AGMQLmZA8HmgpWBsqrn/V6daUALkKnkLb70Fn32CJigIuGXAYqxUdGuI340aC+0
v5Es3puJsHyzf01/AelE4Cdc6bZhQrASJLBh8R3BQToYClmDVSDUQk28o8sl/guA
Z4n303Vj+6Siv1HayPCdV6kpVVnMBAG4+umUbwGmn132N3fgpzLarFF3XyWmS1zh
D/J07iM/rP8GDO9IskHNHd2phrO0G6KzrcFAnTBHjVv+hCBEfzN/no3FNA9AuC36
mwIDAQAB
-----END PUBLIC KEY-----"""

BABI_PARAM = {
    "feature_entrance": "editor",
    "feature_entrance_detail": "editor-feature-text_to_speech",
    "feature_key": "text_to_speech",
    "scenario": "video_editor",
}


def _env_int(name: str, fallback: int) -> int:
    try:
        n = int(os.environ.get(name, ""))
        return n if n > 0 else fallback
    except ValueError:
        return fallback


# ==== Helpers signing (thuần hàm, unit-testable) ====


def compact_json(obj) -> str:
    # Khớp JSON.stringify: no-space separators, giữ UTF-8, giữ thứ tự chèn key
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_ssml(text: str, voice: str, resource_id: str, rate: str) -> str:
    voice_block = (
        f'    <voice name="{voice}" mock_tone_info="" platform="sami" '
        f'resource_id="{resource_id}" emotion="" emotion_scale="0" style="" role="" '
        f'moyin_emotion="" is_clone_tone="false" need_subtitle_timestamp="false">\n'
        f'        <prosody rate="{rate}">{escape_xml(text)}</prosody>\n'
        f"    </voice>"
    )
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">\n'
        + voice_block
        + "\n</speak>"
    )


_public_key = None


def rsa_sign(sign_input: str) -> str:
    """RSA encrypt PKCS#1 v1.5 bằng public key nhúng → base64 (capcut.ts:106-111)."""
    global _public_key
    if _public_key is None:
        _public_key = serialization.load_pem_public_key(TTS_SIGN_PUBLIC_KEY_PEM)
    encrypted = _public_key.encrypt(sign_input.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("ascii")


def payload_sign(ssml: str, extra_info: str, device_id: str, app_id: str) -> str:
    sign_input = (
        f"appid:{app_id}&did:{device_id}&creditDisable:false"
        f"&ssml:{md5_hex(ssml)}&extraInfo:{extra_info}"
    )
    return rsa_sign(sign_input)


def sign_header(path: str, appvr: str, device_time: str, tdid: str) -> str:
    # MD5("9e2c|{7 ký tự cuối path không query}|3|{appvr}|{deviceTime}|{tdid}|11ac")
    clean = path.split("?")[0]
    return md5_hex(f"9e2c|{clean[-7:]}|3|{appvr}|{device_time}|{tdid}|11ac")


def trace_id() -> str:
    seed = pysecrets.token_hex(16)  # 32 hex chars
    return f"00-{seed}-{seed[:16]}-01"


# ==== Request building ====


def common_query(device: dict, babi_param, include_region: bool) -> str:
    q = {
        "app_name": device["app_name"],
        "device_type": device["device_type"],
        "os_version": device["os_version"],
        "channel": device["channel"],
        "version_name": device["version_name"],
        "device_brand": device["device_brand"],
        "device_id": device["device_id"],
        "iid": device["iid"],
        "version_code": device["version_code"],
        "device_platform": device["device_platform"],
        "aid": device["aid"],
    }
    if include_region:
        q["region"] = device["region"]
    if babi_param is not None:
        q["babi_param"] = compact_json(babi_param)
    return urlencode(q)


def base_headers(device: dict, body_text: str, appid: bool) -> dict:
    now = str(int(time.time()))
    headers = {
        "content-type": "application/json",
        "appvr": device["appvr"],
        "ch": device["channel"],
        "device-time": now,
        "lan": device["lan"],
        "loc": device["loc"],
        "pf": device["pf"],
        "sign-ver": "1",
        "tdid": device["tdid"],
        "x-ss-stub": md5_hex(body_text),
        "x-ss-dp": device["aid"],
        "x-khronos": now,
        "x-tt-trace-id": trace_id(),
        "user-agent": "Cronet/TTNetVersion:1d7cc3b1 2025-07-16 QuicVersion:52c2b40d 2025-04-03",
        "accept-encoding": "gzip, deflate",
        "store-country-code": device["loc"].lower(),
        "store-country-code-src": "did",
        "is-dispatch-us-ttp": "0",
        "is-app-region-us-ttp": "0",
    }
    if appid:
        headers["app-sdk-version"] = device["appvr"]
        headers["appid"] = device["aid"]
    return headers


def build_new_request(text: str, voice: str, resource_id: str, rate: str, device: dict, base_url: str) -> dict:
    ssml = build_ssml(text, voice, resource_id, rate)
    extra_info = compact_json({"benefit_info": {}})
    payload = {
        "audio_format": "mp3",
        "babi_param": compact_json(BABI_PARAM),
        "credit_disable": False,
        "extra_info": extra_info,
        "need_merge_voice": False,
        "need_subtitle_timestamp": False,
        "scene": "text_to_speech",
        "ssml": ssml,
        "sign": payload_sign(ssml, extra_info, device["device_id"], device["aid"]),
    }
    body = {
        "bind_id": str(uuid.uuid4()),
        "can_queue": True,
        "enter_from": "text_to_speech",
        "tasks": [
            {
                "context": str(uuid.uuid4()),
                "payload": compact_json(payload),
                "req_key": "sami_text_to_speech",
                "task_version": "v3",
            }
        ],
    }
    path = "/lv/v1/common_task/new"
    url = f"{base_url}{path}?{common_query(device, BABI_PARAM, True)}"
    body_text = compact_json(body)
    headers = base_headers(device, body_text, True)
    headers["sign"] = sign_header(path, device["appvr"], headers["device-time"], device["tdid"])
    return {"url": url, "headers": headers, "body_text": body_text}


def build_query_request(task_id: str, token: str, device: dict, base_url: str) -> dict:
    body = {
        "tasks": [
            {"bind_id": "", "id": task_id, "req_key": "sami_text_to_speech", "task_version": "v3", "token": token}
        ]
    }
    path = "/lv/v1/common_task/query"
    url = f"{base_url}{path}?{common_query(device, None, False)}"
    body_text = compact_json(body)
    headers = base_headers(device, body_text, True)
    headers["sign"] = sign_header(path, device["appvr"], headers["device-time"], device["tdid"])
    return {"url": url, "headers": headers, "body_text": body_text}


# ==== HTTP ====


def post_json(req: dict) -> dict:
    res = requests.post(
        req["url"],
        headers=req["headers"],
        data=req["body_text"].encode("utf-8"),
        timeout=REQUEST_TIMEOUT_S,
    )
    if not res.ok:
        raise HttpStatusError(f"CapCut HTTP {res.status_code}", status=res.status_code)
    data = res.json()
    if data.get("ret") != "0":
        # ret!=0 là lỗi nghiệp vụ (vd ret=-6 shark block) — non-retryable, fail nhanh
        reason = data.get("errmsg") or f"ret={data.get('ret')}"
        raise HttpStatusError(f"CapCut API lỗi: {reason}. {HINT}", retryable=False)
    return data


# ==== Device identity ====


def random_snowflake() -> str:
    # 19 chữ số, đầu 1..9 — mô phỏng device_id dạng snowflake ByteDance
    import random as _random

    return str(_random.randint(1, 9)) + "".join(str(_random.randint(0, 9)) for _ in range(18))


def _load_or_create_persisted_device() -> dict:
    try:
        raw = json.loads(DEVICE_STORE_PATH.read_text(encoding="utf-8"))
        if raw.get("device_id"):
            return {**DEFAULT_DEVICE, **raw}
    except (OSError, json.JSONDecodeError):
        pass
    # Seed 1 lần từ app TS nếu tồn tại (identity đã thoát shark block)
    identity = None
    try:
        seed = json.loads(SEED_DEVICE_PATH.read_text(encoding="utf-8"))
        if seed.get("device_id"):
            identity = {"device_id": seed["device_id"], "tdid": seed["tdid"], "iid": seed["iid"]}
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    if identity is None:
        device_id = random_snowflake()
        identity = {"device_id": device_id, "tdid": device_id, "iid": random_snowflake()}
    DEVICE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICE_STORE_PATH.with_suffix(f".{uuid.uuid4()}.tmp")
    tmp.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    os.replace(tmp, DEVICE_STORE_PATH)
    return {**DEFAULT_DEVICE, **identity}


_device_cache: dict | None = None


def load_device() -> dict:
    """Memoize per-process; CAPCUT_DEVICE_JSON (env, path file) thắng tất cả."""
    global _device_cache
    if _device_cache is not None:
        return _device_cache
    override_path = os.environ.get("CAPCUT_DEVICE_JSON")
    if override_path:
        try:
            raw = json.loads(Path(override_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise RuntimeError(f"Không đọc được CAPCUT_DEVICE_JSON ({override_path}): {err}") from err
        _device_cache = {**DEFAULT_DEVICE, **raw}
    else:
        _device_cache = _load_or_create_persisted_device()
    return _device_cache


# ==== Synth flow ====


def _poll_audio_url(task_id: str, token: str, device: dict, base_url: str) -> str:
    total_timeout_ms = _env_int("CAPCUT_TIMEOUT_MS", DEFAULT_TOTAL_TIMEOUT_MS)
    poll_interval_ms = _env_int("CAPCUT_POLL_INTERVAL_MS", DEFAULT_POLL_INTERVAL_MS)
    deadline = time.time() + total_timeout_ms / 1000
    while time.time() < deadline:
        data = with_retry(lambda: post_json(build_query_request(task_id, token, device, base_url)))
        tasks = (data.get("data") or {}).get("tasks") or []
        task = tasks[0] if tasks else {}
        status = task.get("status")
        if status == "succeed":
            payload = task.get("payload")
            if not payload:
                raise RuntimeError(f"CapCut trả về rỗng dù task thành công. {HINT}")
            parsed = json.loads(payload)
            subtitles = parsed.get("audio_subtitles") or []
            audio = subtitles[0] if subtitles else {}
            if not audio.get("speech_url"):
                raise RuntimeError(f"CapCut không có URL audio trong kết quả. {HINT}")
            return audio["speech_url"]
        if status in ("failed", "fail"):
            reason = f"{task.get('err_msg')} (err_code {task.get('err_code')})" if task.get("err_msg") else "không rõ"
            raise RuntimeError(f"CapCut synth thất bại: {reason}. {HINT}")
        time.sleep(poll_interval_ms / 1000)
    raise RuntimeError(f"CapCut synth quá thời gian chờ ({total_timeout_ms // 1000}s). {HINT}")


def _download_audio(url: str) -> bytes:
    def fetch() -> bytes:
        res = requests.get(url, timeout=REQUEST_TIMEOUT_S)
        if not res.ok:
            raise HttpStatusError(f"CapCut tải audio HTTP {res.status_code}", status=res.status_code)
        return res.content

    audio = with_retry(fetch)
    if len(audio) == 0:
        raise RuntimeError(f"CapCut trả audio rỗng. {HINT}")
    return audio


def resolve_voice_id(cfg: dict) -> str:
    """Giọng hiệu dụng — dùng CHUNG cho cache key và synth (capcut.ts:421-425)."""
    return cfg.get("voice_id") or os.environ.get("CAPCUT_VOICE") or DEFAULT_VOICE


def synth(text: str, cfg: dict) -> bytes:
    base_url = (os.environ.get("CAPCUT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    voice = resolve_voice_id(cfg)
    resource_id = next(
        (v["resource_id"] for v in CAPCUT_VOICES if v["id"] == voice),
        os.environ.get("CAPCUT_RESOURCE_ID") or DEFAULT_RESOURCE_ID,
    )
    device = load_device()

    created = with_retry(lambda: post_json(build_new_request(text, voice, resource_id, "1.0", device, base_url)))
    tasks = (created.get("data") or {}).get("tasks") or []
    task = tasks[0] if tasks else {}
    if not task.get("id") or not task.get("token"):
        raise RuntimeError(f"CapCut không tạo được task (thiếu id/token). {HINT}")
    audio_url = _poll_audio_url(task["id"], task["token"], device, base_url)
    return _download_audio(audio_url)
