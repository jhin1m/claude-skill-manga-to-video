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
để vận hành. Orchestrator (bạn) làm stage NGỮ NGHĨA (đọc ảnh, viết kịch bản); scripts làm stage
CƠ HỌC (ingest/validate/tts/render).

File này dùng chung cho Claude Code và Codex: bản ở `~/.codex/skills/manga-to-video/SKILL.md` là
**bản sao byte-for-byte** (Codex không đi theo symlink). Engine, `references/`, `schemas/`,
`.secrets.json` và cache CapCut đều trỏ về `~/.claude/skills/manga-to-video/` — cố ý, để chạy từ
đâu cũng cho kết quả y hệt và chỉ có MỘT identity CapCut.

**Bảo trì skill** — sửa `SKILL.md` hay bất cứ file nào trong `scripts/` xong thì chạy:

```bash
~/.claude/skills/.venv/bin/python3 ~/.claude/skills/manga-to-video/tests/run_all.py
```

Nó gộp: smoke validate, auto-region, **parity với snapshot TS** (`schemas/parity.json`) và
`sync_codex_facade.py --check`. Facade lệch thì chạy `scripts/sync_codex_facade.py` (không cờ)
để chép đè. Snapshot lệch thì sửa **Python theo TS** rồi chạy `pnpm gen:parity` bên
`content-extractor` — TS là nguồn chân lý, không sửa ngược.

## Prereqs — kiểm tra nhanh trước khi chạy

```bash
~/.claude/skills/.venv/bin/python3 -c "import PIL, numpy, fitz, jsonschema, requests, cryptography; print('deps OK')"
/opt/homebrew/bin/ffmpeg -version | head -1   # ffmpeg hệ thống (bất kỳ build nào; thiếu libass chỉ mất burn-in)
```

Deps thiếu: `~/.claude/skills/.venv/bin/python3 -m pip install pillow numpy pymupdf jsonschema requests cryptography`

**Sandbox (riêng Codex, ĐÃ KIỂM CHỨNG bằng chạy thật):**

- Cần **`workspace-write`**. Mặc định của `codex exec` là read-only + approval `never` → chặn
  cả việc tạo workdir *bên trong* cwd. Phiên tương tác thường đã là workspace-write.
- Cần **network access**. `workspace-write` mặc định CHẶN mạng: `vision.py` và `tts.py` sẽ chết
  với `lỗi mạng: ... Failed to resolve 'api.z.ai'`. Bật bằng
  `-c sandbox_workspace_write.network_access=true` (hoặc đặt sẵn trong `~/.codex/config.toml`).
- Chạy interpreter/scripts và đọc `.secrets.json` + `references/` ở `~/.claude/...` (ngoài
  workspace) thì **được**, không bị chặn — đã kiểm chứng.
- Thấy `PermissionError: Operation not permitted` khi ghi → sandbox read-only, không phải lỗi
  pipeline. Thấy `Failed to resolve` → thiếu network access.

**Secrets** — env hoặc file `~/.claude/skills/manga-to-video/.secrets.json` (map phẳng
`{"ZAI_API_KEY": "...", "ELEVENLABS_API_KEY": "..."}`). KHÔNG hardcode, KHÔNG in giá trị ra log.

| Key | Cần cho | Thiếu thì sao |
|---|---|---|
| `ZAI_API_KEY` | vision (`vision.py`) | xem "Không có `ZAI_API_KEY`" ở bước 2 — Codex thì DỪNG |
| `ELEVENLABS_API_KEY` | TTS ElevenLabs | dùng CapCut (default, free, không cần key) |

## Model policy (tường minh)

| Việc | Ai làm |
|---|---|
| Vision đọc slice | `vision.py` → **z.ai glm-4.5v** chạy inline |
| Viết kịch bản + nghiệm thu cuối | model chính của session (khuyến nghị model mạnh nhất có sẵn) |
| Sửa lỗi validate | orchestrator tự sửa inline |
| ingest/vision/validate/tts/render | orchestrator chạy inline qua shell — KHÔNG delegate |

Lý do đọc slice bằng z.ai: một chương vài trăm slice là việc thuần cơ học — đốt hàng trăm
nghìn token model đắt cho nó là phí. GLM rẻ, đủ tốt, và app TS vốn đã dùng đúng model này.

## Series mode — bộ truyện nhiều chương (tuỳ chọn)

Input là 1 chương của bộ truyện (`MangaX/chapter-N/`) → kiểm tra `../.mtv/`:

- **Có `.mtv/`** → series mode: workdir = `<series>/.mtv/workdirs/<chapter-slug>/`; làm
  **bước 0** trước Ingest và **bước 9** sau Nghiệm thu (dưới đây).
- **Không có** → hỏi user "chương này thuộc bộ truyện, muốn bật series-memory?" — đồng ý thì
  khởi tạo `.mtv/` (hỏi tên bộ) rồi vào series mode; không thì chạy mode chương đơn y cũ.
- Format mọi file series + quy tắc diff-update/stale: **ĐỌC
  `~/.claude/skills/manga-to-video/references/series.md` trước khi tạo/cập nhật** — đó là nguồn
  chân lý.

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

```bash
export PYTHONDONTWRITEBYTECODE=1   # tránh ghi __pycache__ ngoài workspace (sandbox Codex)
PY=~/.claude/skills/.venv/bin/python3
SCRIPTS=~/.claude/skills/manga-to-video/scripts
```

Workdir: thư mục mới do user chọn (hoặc đề xuất cạnh input, vd `<input>-video/`);
series mode dùng `<series>/.mtv/workdirs/<chapter-slug>/`.

### 1. Ingest

```bash
$PY $SCRIPTS/ingest.py --input <folder-ảnh|file.pdf> --workdir <W>
```

Đọc output: số slice + kích thước canvas. Báo user số slice trước khi tiếp.
Tuỳ chọn: `--split-height 1600` `--no-trim` `--pdf-scale 2`.

### 2. Vision — đọc slice

**z.ai, chạy inline, KHÔNG delegate.**

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

**Không có `ZAI_API_KEY`** (script fail rõ ràng đúng lý do đó) — kiểm môi trường TRƯỚC, rồi
mới hành động:

- Môi trường **có** subagent `manga-vision-reader` (Claude Code): chia batch **5-8 slice**,
  spawn tối đa 2-3 batch song song. Prompt giao việc PHẢI có: workdir tuyệt đối, danh sách
  slice id, ngữ cảnh chương. Nhận reply tóm tắt (agent KHÔNG dump JSON). Sau mỗi batch DONE,
  **orchestrator** cập nhật `manifest.json` — agent không được sửa manifest (tránh race).
- Môi trường **không có** agent đó (Codex): **DỪNG**, báo user khai key vào
  `~/.claude/skills/manga-to-video/.secrets.json`. Đừng đọc ảnh tay thay thế — vài trăm slice
  đốt sạch context.

### 3. Validate content

```bash
$PY $SCRIPTS/validate.py --workdir <W> --stage content
```

Lỗi (JSON lines) → sửa: file thiếu/hỏng thì chạy lại `vision.py --only <id>` cho đúng slice đó
(đường fallback subagent — CHỈ Claude Code — thì re-spawn batch cho slice đó, kèm lỗi).

### 4. Viết kịch bản (orchestrator — KHÔNG delegate)

1. Đọc **`~/.claude/skills/manga-to-video/references/style-guide.md` Phần B** (quy tắc CỨNG
   ngôi kể) + `references/schema.md` cùng thư mục đó.
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
# 9:16|16:9|1:1; [--fps 30] [--zoom 1.12] [--bgm file.mp3 --bgm-volume 0.3 --duck-db -12]
# --subs srt|burn|both|none — none = tắt hẳn phụ đề (không burn, không xuất .srt)
# [--motion static|auto] mặc định static (đứng yên); auto = lia/zoom tự suy, dùng --zoom
# [--fill cover|blur] shot lệch khung: cover = full màn hình, cắt mép; blur = vừa khung trên
#   nền blur. Mặc định cover với 9:16, blur với 16:9/1:1
# [--no-auto-region] tắt auto-crop né bubble (mặc định BẬT); --dry-run xem lệnh
# [--subs-preset clean|boxed|punchy|classic|news] preset dựng sẵn, khớp bản web
# [--subs-style '<json>'] style phụ đề; [--subs-fonts-dir <thư mục>] font cho libass
```

Thứ tự đè: **mặc định ◀ `--subs-preset` ◀ `--subs-style`** — preset là điểm khởi đầu, JSON
chỉnh tiếp từng field. Preset đọc từ `schemas/parity.json` (do repo TS xuất) nên chọn *punchy*
trên web rồi render lại bằng skill ra **đúng một kết quả**.

Giá trị ngoài miền hợp lệ bị **kẹp về biên** kèm một dòng cảnh báo stderr, không fail —
`{"size_pct": 45}` ra chữ nửa khung mà ffmpeg vẫn exit 0 nên đây là lưới duy nhất. Field gõ sai
tên cũng được cảnh báo (libass nuốt im lặng field lạ).

**Style phụ đề (`--subs-style`, JSON đè lên preset/mặc định):** `font`, `size_pct`, `color`,
`opacity`, `bold`, `italic`, `underline`, `uppercase`, `letter_spacing`,
`border` (`none|outline|box`), `outline_width`, `outline_color`, `outline_opacity`,
`shadow_depth`, `shadow_color`, `position`, `margin_pct`, `side_margin_pct`,
`max_chars_per_line`. `uppercase` + `max_chars_per_line` áp cho **cả `.srt` rời**.

Ba quirk của libass đã đo và bù sẵn (đồng bộ với `content-extractor/lib/subtitle-style.ts`):

1. `outline_color` là màu **viền** ở `border=outline` nhưng là màu **nền hộp** ở `border=box`;
   `shadow_color` luôn là màu bóng.
2. Cỡ chữ chia **432** chứ không phải `PlayResY=288` — libass render nhỏ hơn 1,5 lần.
3. `force_style` dùng hệ căn lề **SSA v4 cũ**: 2 = dưới-giữa, 6 = trên-giữa, 10 = giữa-giữa
   (numpad V4+ 8/5 sẽ ra sai cả trục dọc lẫn căn lề ngang).

Font tiếng Việt: trỏ `--subs-fonts-dir` (hoặc `SUBS_FONTS_DIR`) tới thư mục chứa TTF —
ví dụ `content-extractor/public/fonts` của app Next. Không có thì libass lấy font hệ thống,
và nhiều font caption phổ biến (Anton, Bebas Neue, Archivo Black) **thiếu dấu tiếng Việt**.
Chọn nhầm một trong số đó thì render cảnh báo rõ trên stderr, vẫn chạy tiếp — libass rơi về
font hệ thống trong im lặng nên không có lỗi nào khác bắn ra.

**Auto-region (né bubble) v2.1:** sau `split_tall`, mỗi shot có thể được gán `region`
heuristic. Manhwa dọc: **giữ full width**, chỉ cắt dọc cửa sổ ít bubble (tối đa ~22%
chiều cao). Không xoá pixel. Không ổn / muốn full panel: `--no-auto-region`.
Đồng bộ với app Next (`lib/auto-region.ts`).

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
described → tiếp đúng chỗ dở. Workdir do môi trường kia tạo dở (Claude Code ↔ Codex) resume
được nguyên vẹn — chung engine, chung cache.

- `ingest.py --resume` chỉ in trạng thái, không phá workdir.
- Vision: chạy lại `vision.py` — slice đã có `content/NNN.json` tự bỏ qua, không tốn call.
  (Đường fallback subagent, chỉ Claude Code: spawn batch cho slice còn `pending`.)
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

Đều nằm ở `~/.claude/skills/manga-to-video/references/`:

- `style-guide.md` — quy tắc vision + kịch bản (ngôi kể CỨNG)
- `schema.md` — hợp đồng JSON + ví dụ
- `series.md` — series-memory: cấu trúc `.mtv/`, format bible/recap, diff-update, stale
- Scripts đều in lỗi dạng JSON line `{"file","path","rule","message"}` và exit ≠ 0
