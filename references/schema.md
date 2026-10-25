# Schema hợp đồng workdir — content/NNN.json + script.json

JSON Schema máy-đọc ở `schemas/content.schema.json` + `schemas/script.schema.json`
(validate bằng `scripts/validate.py`). File này là bản người-đọc kèm ví dụ.

## content/NNN.json (vision.py hoặc subagent fallback ghi, 1 file / slice)

Port `SceneContent` từ `content-extractor/lib/schema.ts:4-8`.

```json
{
  "slice_id": "003",
  "description": "Trong đại sảnh, người phụ nữ trẻ mặc hỉ phục đỏ bước qua ngưỡng cửa, ánh mắt mọi người đổ dồn về phía cô.",
  "dialogue": ["Tân nương đến rồi!", "Nàng ta chính là mẹ kế mới sao?"],
  "note": "Chuyển cảnh từ ngoài phủ vào đại sảnh."
}
```

Quy tắc:
- `slice_id`: đúng 3 chữ số, khớp tên file (`content/003.json` → `"003"`).
- `description`: **không được rỗng**, ngôi ba khách quan (xem style-guide.md).
- `dialogue`: array of string (rỗng `[]` nếu không thoại), dịch tiếng Việt, đúng thứ tự đọc.
- `note`: `""` nếu không có gì đáng lưu ý.
- KHÔNG thêm field lạ (`additionalProperties: false`).

❌ Không hợp lệ: `description` rỗng; `dialogue` là string; thiếu `note`; `slice_id` "3".

## script.json (orchestrator ghi, cả chương)

Rút gọn từ `ScriptSegment` (`schema.ts:188-196`) — shots mặc định full-slice, region null,
motion theo cờ `render.py --motion` (v1 không cho agent chỉ định region/motion).

```json
{
  "version": 1,
  "segments": [
    {"id": "seg-000", "order": 0, "narration": "Nàng vừa gả vào phủ đã trở thành mẹ kế của ba đứa trẻ.", "shot_indices": [0, 1]},
    {"id": "seg-001", "order": 1, "narration": "Trong đại sảnh, mọi ánh mắt đổ dồn về phía tân nương. \"Nàng ta chính là mẹ kế mới sao?\" — tiếng xì xào vang lên.", "shot_indices": [2]}
  ]
}
```

Quy tắc (validate.py fail cứng nếu vi phạm):
- `id` dạng `seg-NNN` theo order (`seg-000`, `seg-001`…); `order` liên tục từ 0.
- `narration` không rỗng sau trim, ngôi ba (xem style-guide.md).
- `shot_indices`: index slice (int ≥ 0) mà đoạn kể phủ, theo thứ tự đọc.
- **Phủ index toàn cục**: mỗi slice index xuất hiện ĐÚNG 1 lần trên toàn bộ segments —
  không sót, không lặp, không vượt biên `[0, số_slice)`.

## Ước lượng thời lượng đọc

`estimate_seconds(narration) = max(1, ceil(số_ký_tự_sau_trim / 15))` — TTS tiếng Việt đọc
~15 ký tự/giây (port `schema.ts:127-131`). `validate.py --stage script` in tổng ước tính để
cân nhắc độ dài video trước khi TTS.
