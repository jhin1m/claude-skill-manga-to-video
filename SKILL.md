---
name: manga-to-video
description: "Tạo video recap (mp4 + SRT, narration tiếng Việt) từ folder ảnh/PDF manga-manhwa. Pipeline A-Z: ingest band-split → vision đọc slice (z.ai GLM) → viết kịch bản → TTS (CapCut/ElevenLabs) → render Ken Burns. Dùng khi user muốn chuyển chương truyện tranh thành video thuyết minh."
metadata:
  author: jhin1m
  version: "1.0.0"
---

# Manga to Video

Chuyển 1 chương manga/manhwa (folder ảnh hoặc PDF) thành video thuyết minh tiếng Việt:
`out/video.mp4` + `out/video.srt` + `out/timeline.json`.

Engine Python port từ `content-extractor/lib` (TypeScript) — scripts tự chứa, KHÔNG cần repo TS
để vận hành. Orchestrator (bạn) làm stage NGỮ NGHĨA (đọc ảnh qua subagent, viết kịch bản);
scripts làm stage CƠ HỌC (ingest/validate/tts/render).

## Prereqs — kiểm tra nhanh trước khi chạy

```bash
~/.claude/skills/.venv/bin/python3 -c "import PIL, numpy, fitz, jsonschema, requests, cryptography; print('deps OK')"
/opt/homebrew/bin/ffmpeg -version | head -1   # ffmpeg hệ thống (bất kỳ build nào; thiếu libass chỉ mất burn-in)
```

Deps thiếu: `~/.claude/skills/.venv/bin/python3 -m pip install pillow numpy pymupdf jsonschema requests cryptography`

**Secrets** — env hoặc file `~/.claude/skills/manga-to-video/.secrets.json` (map phẳng
`{"ZAI_API_KEY": "...", "ELEVENLABS_API_KEY": "..."}`). KHÔNG hardcode, KHÔNG in giá trị ra log.

| Key | Cần cho | Thiếu thì sao |
|---|---|---|
| `ZAI_API_KEY` | vision (`vision.py`) | lùi về subagent `manga-vision-reader` |
| `ELEVENLABS_API_KEY` | TTS ElevenLabs | dùng CapCut (default, free, không cần key) |

## Model policy (tường minh)

| Việc | Ai làm |
|---|---|
| Vision đọc slice | `vision.py` → **z.ai glm-4.5v** chạy inline. Không có key mới lùi về subagent `manga-vision-reader` (sonnet) |
| Viết kịch bản + nghiệm thu cuối | model chính của session (khuyến nghị **Opus trở lên**) |
| Sửa lỗi validate | orchestrator tự sửa inline |
| ingest/vision/validate/tts/render | orchestrator chạy inline qua Bash — KHÔNG delegate |

Lý do đọc slice bằng z.ai: một chương vài trăm slice là việc thuần cơ học — đốt hàng trăm
nghìn token model đắt cho nó là phí. GLM rẻ, đủ tốt, và app TS vốn đã dùng đúng model này.

## Series mode — bộ truyện nhiều chương (tuỳ chọn)

Input là 1 chương của bộ truyện (`MangaX/chapter-N/`) → kiểm tra `../.mtv/`:

- **Có `.mtv/`** → series mode: workdir = `<series>/.mtv/workdirs/<chapter-slug>/`; làm
  **bước 0** trước Ingest và **bước 9** sau Nghiệm thu (dưới đây).
- **Không có** → hỏi user "chương này thuộc bộ truyện, muốn bật series-memory?" — đồng ý thì
  khởi tạo `.mtv/` (hỏi tên bộ) rồi vào series mode; không thì chạy mode chương đơn y cũ.
- Format mọi file series + quy tắc diff-update/stale: **ĐỌC `references/series.md` trước khi
  tạo/cập nhật** — đó là nguồn chân lý.

**Bước 0 — nạp context**: ghép khối context từ `bible.md` + recap 2 chương liền trước
(natural-sort; xem series.md). Khối này tiêm NGUYÊN VĂN vào prompt vision batch (bước 2) và
đọc trước khi viết kịch bản (bước 4). **Stale check**: re-run chương mà recap của nó đã bị
chương sau dùng (`context_recaps_used` trong series.json) → cảnh báo user trước khi ghi đè.

**Bước 9 — cập nhật memory** (sau khi bước 8 pass): đọc lại `script.json` + `content/*.json`
→ viết `summaries/<chapter-slug>.md` (≤250 từ) → diff-update `bible.md` (CHỈ facts bền, không
xoá entry chương khác) → cập nhật `series.json` → kiểm:

```bash
$PY $SCRIPTS/validate.py --series <series>/.mtv
```

## Workflow A-Z

`PY=~/.claude/skills/.venv/bin/python3`, `SCRIPTS=~/.claude/skills/manga-to-video/scripts`.
Workdir: thư mục mới do user chọn (hoặc đề xuất cạnh input, vd `<input>-video/`);
series mode dùng `<series>/.mtv/workdirs/<chapter-slug>/`.

### 1. Ingest

```bash
$PY $SCRIPTS/ingest.py --input <folder-ảnh|file.pdf> --workdir <W>
```

Đọc output: số slice + kích thước canvas. Báo user số slice trước khi tiếp.
Tuỳ chọn: `--split-height 1600` `--no-trim` `--pdf-scale 2`.

### 2. Vision — đọc slice

**Mặc định: z.ai (inline, KHÔNG delegate).**

```bash
$PY $SCRIPTS/vision.py --workdir <W>
# [--model glm-4.6v] [--context-file ctx.txt] [--concurrency 3] [--force] [--only 003,004]
```

- Ngữ cảnh chương của user và/hoặc khối context series (bước 0) → ghi ra file, truyền
  `--context-file`. Script tự chèn kèm guardrail chống context bleed.
- Script tự resume: slice đã có `content/NNN.json` thì bỏ qua. Slice lỗi → in JSON line
  `describe-failed` + exit 1; chạy lại lệnh là đủ (chỉ đọc lại slice còn thiếu).
- Rate limit Coding Plan chặt theo phút — `1302`/`429`/`5xx` đã tự retry backoff. Vẫn dính
  thì hạ `--concurrency 2`.
- Script là single-writer của `manifest.json` trong stage này (`slices[].status` +
  `stages.content`) — nó chạy inline nên không có race với ai.

**Fallback khi không có `ZAI_API_KEY`** (script sẽ fail rõ ràng nói đúng điều này): chia batch
**5-8 slice**, spawn subagent `manga-vision-reader` (song song tối đa 2-3 batch). Prompt giao
việc PHẢI có: workdir tuyệt đối, danh sách slice id, ngữ cảnh chương. Nhận reply tóm tắt (agent
KHÔNG dump JSON). Sau mỗi batch DONE, **orchestrator** cập nhật `manifest.json` — agent không
được sửa manifest (tránh race giữa các batch).

### 3. Validate content

```bash
$PY $SCRIPTS/validate.py --workdir <W> --stage content
```

Lỗi (JSON lines) → sửa: file thiếu/hỏng thì re-spawn batch cho đúng slice đó, kèm lỗi.

### 4. Viết kịch bản (orchestrator — KHÔNG delegate)

1. Đọc **`references/style-guide.md` Phần B** (quy tắc CỨNG ngôi kể) + `references/schema.md`.
   Series mode: đọc lại khối context bước 0 — xưng hô/tên PHẢI khớp bible, mở đầu nối mạch recap.
2. Đọc TOÀN BỘ `content/*.json` — nắm cả chương rồi mới viết (gộp/tách khung theo diễn biến).
3. Ghi `<W>/script.json` đúng schema — mỗi slice index đúng 1 lần, narration ngôi ba.

### 5. Validate script — loop đến khi pass

```bash
$PY $SCRIPTS/validate.py --workdir <W> --stage script
```

Sửa inline theo từng lỗi (`index-missing`/`index-duplicate`/`narration-empty`…). Pass sẽ in
tổng thời lượng ước tính — báo user nếu bất thường (quá ngắn/dài).

### 6. TTS

```bash
$PY $SCRIPTS/tts.py --workdir <W>                      # default: CapCut (free, giọng Việt)
# hoặc: --provider elevenlabs [--voice ID] [--model M]; --force để synth lại
```

Cache theo segment — chạy lại không tốn call. Lỗi `ret=-6` (shark block): xoá
`~/.claude/skills/manga-to-video/.cache/capcut-device.json` để random identity mới, hoặc đổi provider.

### 7. Render

```bash
$PY $SCRIPTS/render.py --workdir <W> --aspect 9:16 --subs both
# 9:16|16:9|1:1; [--fps 30] [--zoom 1.12] [--bgm file.mp3 --bgm-volume 0.3 --duck-db -12]; --dry-run xem lệnh
```

ffmpeg thiếu libass → tự bỏ burn-in, vẫn có `.srt` rời (cảnh báo trên stderr, không fail).

**RÀNG BUỘC: mỗi workdir chỉ 1 render tại một thời điểm.** `out/.shots` và `out/video.mp4`
dùng chung, không namespace theo aspect/pid — chạy song song 2 aspect trên cùng workdir sẽ
hỏng output. Cần nhiều aspect: render tuần tự, copy `out/` ra ngoài giữa các lần.

### 8. Nghiệm thu + bàn giao

- `ffprobe` duration/streams của `out/video.mp4`; mở video xem sync audio/hình.
- Đối chiếu `out/video.srt` với script (timing theo segment).
- Báo user: đường dẫn `out/`, tổng thời lượng, số segment, provider TTS đã dùng.
- Series mode: tiếp tục **bước 9** (cập nhật memory — xem Series mode ở trên).

## Resume — đứt giữa chừng

Đọc `manifest.json`: `stages` cho biết stage nào dở, `slices[].status` cho biết slice nào đã
described → tiếp đúng chỗ dở:
- `ingest.py --resume` chỉ in trạng thái, không phá workdir.
- Vision: chạy lại `vision.py` — slice đã có `content/NNN.json` tự bỏ qua, không tốn call.
  (Fallback subagent: chỉ spawn batch cho slice còn `pending`.)
- `script.json` đã có → validate rồi đi tiếp. Audio cache tự resume (hit không gọi mạng).
- Render lại không cần gọi TTS (đọc audio-manifest.json).

## Cấu trúc workdir (hợp đồng)

```
W/
  manifest.json        # single-writer: scripts/orchestrator (vision agent KHÔNG sửa)
  slices/NNN.png
  content/NNN.json     # vision.py (hoặc subagent fallback) ghi
  script.json          # orchestrator ghi
  audio/<sha256>.mp3+.json, audio-manifest.json
  out/video.mp4, video.srt, timeline.json
```

## Tài liệu

- `references/style-guide.md` — quy tắc vision + kịch bản (ngôi kể CỨNG)
- `references/schema.md` — hợp đồng JSON + ví dụ
- `references/series.md` — series-memory: cấu trúc `.mtv/`, format bible/recap, diff-update, stale
- Scripts đều in lỗi dạng JSON line `{"file","path","rule","message"}` và exit ≠ 0
