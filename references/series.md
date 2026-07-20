# Series-memory: hợp đồng `.mtv/` + quy tắc bible/recap

Nguồn chân lý format cho mode series (bộ truyện nhiều chương). Orchestrator ĐỌC FILE NÀY
trước khi tạo/cập nhật bất kỳ file series nào.

## Cấu trúc

```
MangaX/                          # series folder — user tự đặt tên
  chapter-1.1/  *.jpg|pdf        # mỗi chương 1 thư mục ảnh nguồn
  chapter-1.2/  ...
  .mtv/
    series.json                  # index — schema: schemas/series.schema.json
    bible.md                     # facts BỀN của cả bộ (cap 2000 từ)
    summaries/<chapter-slug>.md  # recap diễn biến từng chương (cap 250 từ)
    workdirs/<chapter-slug>/     # workdir per-chapter (contract cũ, không đổi)
```

Thứ tự chương = natural-sort tên thư mục (`1.1 < 1.2 < 2 < 10`). KHÔNG sort chuỗi thô.

## bible.md — CHỈ facts bền

Chứa thứ ổn định xuyên chương. **CẤM diễn biến/sự kiện** (diễn biến thuộc summaries).
Cap **2000 từ** — vượt là dấu hiệu đã lẫn diễn biến, phải gọt lại.

```markdown
# Bible: <tên bộ truyện>

## Nhân vật
- **<Tên>** — <vai trò 1 dòng>. Xưng hô: <tự xưng / gọi người khác thế nào>. Quan hệ: <với ai, là gì>.

## Bối cảnh & thiết lập
- <thế giới, thời đại, quy tắc đặc biệt — mỗi ý 1 dòng>

## Quy tắc xưng hô
- <cặp nhân vật> → <cách xưng hô hai chiều> (vd: Vân Chi ↔ Mặc Trạch: "ta – chàng" / "ta – nàng")
```

### Quy tắc diff-update (sau mỗi chương)

1. CHỈ thêm entry mới hoặc sửa entry liên quan trực tiếp chương vừa chạy.
2. **KHÔNG xoá / viết lại entry của chương khác.** Quan hệ thay đổi (cưới, phản bội…) → sửa
   dòng quan hệ, không xoá nhân vật.
3. Không chắc một fact có bền không → để ở summary, đừng đưa vào bible.
4. User có thể đã sửa tay bible — tôn trọng nội dung hiện có, chỉ diff phần chương mình vừa chạy.

## summaries/<chapter-slug>.md — recap diễn biến

Cap **250 từ**. Ngôi ba, thuật theo nhân quả (ai làm gì → dẫn tới gì), đủ để người chưa đọc
chương nắm mạch. Không bình luận, không spoil ý đồ chương sau.

```markdown
# Recap <chapter-slug>: <tên bộ truyện>

<3-6 câu diễn biến chính theo thứ tự nhân quả. Nêu tên nhân vật đúng bible.>

Kết chương: <tình huống/cliffhanger đang treo — 1 câu>.
```

Recap do TS app (nút "Xuất recap chương") sinh KHÔNG có dòng header `# Recap …` (app không
biết slug/tên bộ) — orchestrator/user tự thêm header khi đặt file vào `summaries/`; validate
không kiểm header nên thiếu cũng không fail.

## Khối context tiêm vào chương N

Ghép theo thứ tự, đưa vào prompt vision batch + prompt biên kịch (cùng một khối):

```
## Ngữ cảnh bộ truyện (chỉ dùng gọi đúng tên/xưng hô — CẤM mô tả thứ không có trong ảnh)
<toàn bộ bible.md>

## Diễn biến các chương trước
<recap N-2 nếu có>
<recap N-1>
```

- Lấy đúng **2 recap gần nhất** trước chương hiện tại theo natural-sort. Chương đầu: bỏ mục
  recap; chưa có bible: bỏ luôn khối (hành vi y mode chương đơn).
- Guardrail giữ nguyên wording trên — chống context bleed vào narration.

## series.json + stale warning

- Schema: `schemas/series.schema.json`. Validate: `validate.py --series <series>/.mtv`.
- Chương pass nghiệm thu → append/update entry `{slug, status: "done", completed_at,
  context_recaps_used: [slug các recap đã tiêm]}`.
- **Stale check khi re-run chương X**: nếu tồn tại chương khác có `X ∈ context_recaps_used`
  → cảnh báo user "recap của X đã được chương <Y> dùng làm context — chạy lại X có thể làm
  <Y> lệch mạch; muốn đồng bộ phải chạy lại cả <Y>". Chờ user xác nhận rồi mới ghi đè.
