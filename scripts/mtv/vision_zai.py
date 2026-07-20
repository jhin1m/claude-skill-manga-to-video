# Port từ content-extractor/lib/vision.ts (PROMPT :63-77, buildVisionPrompt :83-95,
# boundForVision :361-368, describeImage :370-400, stripNoise/sliceBalancedObject/extractJson
# :268-347) — đồng bộ 2 nơi khi fix bug.
#
# Provider vision z.ai GLM cho skill: gọi /chat/completions kiểu OpenAI với ảnh base64.
# Endpoint mặc định là **Coding Plan** (/api/coding/paas/v4); endpoint trả-theo-token
# (/api/paas/v4) trả `1113 Insufficient balance` với key Coding Plan.
#
# KHÁC TS có chủ ý: luôn re-encode slice sang JPEG q80 trước khi gửi (TS trả nguyên buffer khi
# ảnh đã trong ngưỡng, vì ảnh của app vốn đã là JPEG; slice của skill là PNG nên gửi thô sẽ
# nặng hơn nhiều token mà không nét hơn). `thinking:{type:'disabled'}` luôn bật — module này
# chỉ phục vụ GLM, không có provider khác để phải gate như bên TS.

from __future__ import annotations

import base64
import io
import json
import re

import requests
from PIL import Image

from .http_retry import HttpStatusError, with_retry
from .ingest_core import JPEG_QUALITY, MAX_EDGE_DEFAULT
from .secrets import get_secret

DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
DEFAULT_MODEL = "glm-4.5v"  # hoặc glm-4.6v; glm-5v-turbo chưa mở cho Coding Plan (lỗi 1311)
API_KEY_NAME = "ZAI_API_KEY"
REQUEST_TIMEOUT_SEC = 180

# Mã lỗi z.ai trả trong body (HTTP 200) — rate limit theo phút của Coding Plan rất chặt.
RETRYABLE_BODY_CODES = {"1302", "1303"}

# Nguyên văn PROMPT vision.ts:63-77. Quy tắc này sống ở 3 nơi — sửa là phải sửa cả 3:
# vision.ts (app TS), file này (vision.py), references/style-guide.md Phần A (subagent fallback).
PROMPT = """Bạn là công cụ trích xuất nội dung trang manga cho pipeline thuyết minh.
ĐỌC KỸ trang truyện trong ảnh rồi trả về DUY NHẤT một JSON thô (không markdown, không lời dẫn, không giải thích) đúng dạng:
{"description": string, "dialogue": string[], "note": string}

Quy tắc — MỌI giá trị bằng TIẾNG VIỆT (kể cả dialogue: PHẢI dịch, KHÔNG chép nguyên văn tiếng Anh/Hàn/Nhật):
- "description": mô tả những gì đang diễn ra trong trang (nhân vật, hành động, bối cảnh, cảm xúc). Độ dài thích ứng theo nội dung: trang đơn giản thì ngắn gọn, trang nhiều diễn biến thì chi tiết hơn. Chỉ mô tả những gì nhìn thấy, không suy diễn thêm, không viết lời bình sẵn. Luôn mô tả KHÁCH QUAN ở ngôi thứ ba (gọi nhân vật bằng tên nếu biết, hoặc "chàng trai/cô gái/người đàn ông…") — kể cả khi trang là độc thoại nội tâm ngôi "tôi/ta", description KHÔNG được viết ở ngôi thứ nhất.
- "dialogue": lời thoại trong các khung/bong bóng thoại, DỊCH sang tiếng Việt (ngôn ngữ gốc có thể là Nhật/Hàn/Anh/Trung), xếp theo đúng thứ tự đọc. Để mảng rỗng [] nếu trang không có thoại.
- "note": để chuỗi rỗng "" trừ khi thật sự có điều đáng lưu ý: hiệu ứng âm thanh/từ tượng thanh nổi bật, chuyển cảnh, chi tiết nền quan trọng cho mạch truyện, hoặc cảnh báo ảnh mờ/chữ khó đọc (độ tin cậy thấp)."""

# Guardrail chống context bleed — nguyên văn buildVisionPrompt (vision.ts:88-94).
CONTEXT_TEMPLATE = """

Ngữ cảnh chương (người dùng cung cấp — CHỈ dùng để gọi đúng tên nhân vật và chọn đúng đại từ xưng hô khi dịch thoại; KHÔNG mô tả nhân vật/sự kiện không xuất hiện trong ảnh, KHÔNG chép ngữ cảnh này vào description):
{context}"""

_THINK_BLOCK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.IGNORECASE | re.DOTALL)
_THINK_HEAD_RE = re.compile(r"^.*?</(?:think|thinking|reasoning)>", re.IGNORECASE | re.DOTALL)
_FENCED_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"```(?:json)?\s*(.*)$", re.IGNORECASE | re.DOTALL)


class ModelOutputError(Exception):
    """Output model không parse được — KHÔNG retryable theo status, nhưng gọi lại có thể khác."""


def build_prompt(context: str = "") -> str:
    trimmed = (context or "").strip()
    return PROMPT if not trimmed else PROMPT + CONTEXT_TEMPLATE.format(context=trimmed)


def strip_noise(raw: str) -> str:
    """Port stripNoise (vision.ts:268-280): gỡ khối suy nghĩ + fence (kể cả fence chưa đóng)."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_HEAD_RE.sub("", text).strip()
    fenced = _FENCED_RE.search(text)
    if fenced:
        return fenced.group(1).strip()
    open_fence = _OPEN_FENCE_RE.search(text)
    if open_fence:
        return open_fence.group(1).strip()
    return text


def slice_balanced_object(text: str) -> str | None:
    """Port sliceBalancedObject (vision.ts:286-311): cắt object JSON đầu tiên CÂN BẰNG ngoặc.

    Cặp find('{')/rfind('}') thô sai hai kiểu: ôm nhầm khi model in thêm chữ SAU JSON, và khi
    output bị cắt giữa chừng thì tóm '}' của phần tử con → slice lệch ngoặc.
    None = không có object nào đóng đủ (gần như luôn là output bị cắt).
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if in_string and ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(raw: str) -> dict:
    """Port extractJson (vision.ts:315-333) — chịu fence, lời dẫn trước/sau, khối suy nghĩ."""
    text = strip_noise(raw)
    if not text:
        raise ModelOutputError("Model trả về không đúng JSON (rỗng)")
    candidate = slice_balanced_object(text)
    if candidate is None:
        raise ModelOutputError(
            "Model trả về không đúng JSON (không có object đóng đủ ngoặc — nhiều khả năng "
            "output bị cắt giữa chừng)"
        )
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as err:
        raise ModelOutputError(f"Model trả về không đúng JSON ({err.msg})") from err


def parse_scene(raw: str) -> dict:
    """Port parseSceneJSON (vision.ts:335-347): ép kiểu mềm, dialogue vô hướng → mảng 1 phần tử."""
    obj = extract_json(raw)
    dialogue = obj.get("dialogue")
    if isinstance(dialogue, list):
        items = [str(d) for d in dialogue]
    elif dialogue in (None, ""):
        items = []
    else:
        items = [str(dialogue)]
    return {
        "description": "" if obj.get("description") is None else str(obj["description"]),
        "dialogue": items,
        "note": "" if obj.get("note") is None else str(obj["note"]),
    }


def bound_for_vision(data: bytes, max_edge: int = MAX_EDGE_DEFAULT) -> bytes:
    """Chốt hợp đồng MAX_EDGE + nén JPEG q80 trước khi gửi (port boundForVision :361-368)."""
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    if w > max_edge or h > max_edge:
        scale = min(max_edge / w, max_edge / h)
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def resolve_api_key() -> str:
    return get_secret(API_KEY_NAME)


def _post(url: str, api_key: str, body: dict) -> dict:
    """1 lượt gọi — dịch lỗi sang HttpStatusError để with_retry phân loại 429/5xx."""
    try:
        res = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=REQUEST_TIMEOUT_SEC,
        )
    except requests.RequestException as err:
        raise HttpStatusError(f"lỗi mạng: {err}") from err  # status None → with_retry coi là retryable

    if res.status_code != 200:
        # Body lỗi của z.ai là JSON có .error.message — lấy ra cho log đọc được
        detail = res.text[:300]
        try:
            payload = res.json()
            detail = str(payload.get("error", {}).get("message") or detail)
        except ValueError:
            pass
        raise HttpStatusError(f"HTTP {res.status_code}: {detail}", status=res.status_code)

    try:
        payload = res.json()
    except ValueError as err:
        raise HttpStatusError(f"body không phải JSON: {res.text[:200]}") from err

    # Rate limit của Coding Plan có thể về dạng body-code (HTTP 200) thay vì 429 — với mã đã
    # biết là tạm thời thì ép retryable, còn lại fail nhanh (đừng đập lại lỗi cấu hình).
    err_obj = payload.get("error")
    if isinstance(err_obj, dict) and err_obj:
        code = str(err_obj.get("code", ""))
        raise HttpStatusError(
            f"z.ai lỗi {code}: {err_obj.get('message', '')}",
            retryable=code in RETRYABLE_BODY_CODES,
        )
    return payload


def describe(png_bytes: bytes, prompt: str, model: str, base_url: str, api_key: str) -> dict:
    """Đọc MỘT slice → {description, dialogue, note}. Ném sau khi with_retry cạn lượt."""
    b64 = base64.b64encode(bound_for_vision(png_bytes)).decode()
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        # GLM là reasoning model (100-300 reasoning token/ảnh) — tắt hẳn giảm ~50% token,
        # JSON trả về vẫn đúng. KHÔNG đặt max_tokens: nhỏ hơn reasoning budget thì content rỗng.
        "thinking": {"type": "disabled"},
    }
    url = f"{base_url.rstrip('/')}/chat/completions"

    def attempt() -> dict:
        payload = _post(url, api_key, body)
        choice = (payload.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        if finish == "length":
            # Output bị cắt do trần token — gọi lại y hệt cũng cắt y hệt, đừng đốt lượt.
            raise HttpStatusError(
                "output bị cắt giữa chừng (finish_reason=length) — giảm kích thước slice "
                "hoặc đổi model",
                retryable=False,
            )
        scene = parse_scene(content)
        if not scene["description"].strip():
            raise ModelOutputError("description rỗng")
        return scene

    return with_retry(attempt)
