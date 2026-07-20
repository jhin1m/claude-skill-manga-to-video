# Style guide — đọc slice (vision) + viết kịch bản

Port NGUYÊN quy tắc từ `content-extractor/lib/vision.ts` (PROMPT :63-77) và `lib/script.ts`
(PROMPT_HEADER :69-85, buildPrompt :88-112) — đồng bộ 2 nơi khi sửa quy tắc.

## Phần A — Quy tắc đọc slice (ghi `content/NNN.json`)

PROMPT thi hành quy tắc này nằm ở 3 nơi, sửa quy tắc phải sửa cả 3: `content-extractor/lib/vision.ts`
(PROMPT), `scripts/mtv/vision_zai.py` (PROMPT), và file này (nguồn cho subagent `manga-vision-reader`).

MỌI giá trị bằng **TIẾNG VIỆT** — kể cả `dialogue`: PHẢI dịch, KHÔNG chép nguyên văn tiếng
Anh/Hàn/Nhật. (Đo 2026-07-20 trên glm-4.5v: bỏ vế nhấn này, thoại bị chép nguyên văn 6/9 lượt.)

- **`description`**: mô tả những gì đang diễn ra trong slice (nhân vật, hành động, bối cảnh,
  cảm xúc). Độ dài **thích ứng** theo nội dung: slice đơn giản thì ngắn gọn, slice nhiều diễn
  biến thì chi tiết hơn. Chỉ mô tả những gì nhìn thấy, **không suy diễn thêm**, không viết lời
  bình sẵn. Luôn mô tả **KHÁCH QUAN ở ngôi thứ ba** (gọi nhân vật bằng tên nếu biết, hoặc
  "chàng trai/cô gái/người đàn ông…") — kể cả khi slice là độc thoại nội tâm ngôi "tôi/ta",
  `description` **KHÔNG được viết ở ngôi thứ nhất**.
- **`dialogue`**: lời thoại trong các khung/bong bóng thoại, **DỊCH sang tiếng Việt** (gốc có
  thể là Nhật/Hàn/Anh/Trung), xếp theo **đúng thứ tự đọc**. Mảng rỗng `[]` nếu không có thoại.
- **`note`**: chuỗi rỗng `""` trừ khi thật sự đáng lưu ý: hiệu ứng âm thanh/từ tượng thanh nổi
  bật, **chuyển cảnh**, chi tiết nền quan trọng cho mạch truyện, hoặc cảnh báo **ảnh mờ/chữ khó
  đọc** (độ tin cậy thấp). `note` ĐƯỢC feed vào bước viết kịch bản — đừng bỏ tín hiệu mạch truyện.

### Ngữ cảnh chương (nếu user cung cấp)

CHỈ dùng để gọi đúng tên nhân vật và chọn đúng đại từ xưng hô khi dịch thoại. **KHÔNG** mô tả
nhân vật/sự kiện không xuất hiện trong ảnh (context bleed), **KHÔNG** chép ngữ cảnh vào description.

## Phần B — Quy tắc viết kịch bản (orchestrator ghi `script.json`)

Vai: biên kịch kiêm người dẫn chuyện cho video recap truyện tranh — lời kể sẽ được đọc
thuyết minh (voice-over).

**Trước khi viết**: đọc TOÀN BỘ `content/*.json` để nắm mạch truyện (ai là ai, sự kiện nối
nhau ra sao) rồi mới đặt bút — kể theo **nhân quả**, không chép máy móc mô tả từng khung.

### Ngôi kể — quy tắc CỨNG

- Lời kể **LUÔN ở ngôi thứ ba**, vai người kể chuyện đứng ngoài câu chuyện: gọi nhân vật bằng
  TÊN hoặc đại từ ngôi ba phù hợp (hắn, y, cô, nàng, cô ta, ông ta…).
- **KHÔNG nhập vai nhân vật**: cấm dùng "tôi/ta/mình" làm chủ ngữ của lời kể, kể cả khi khung
  là độc thoại nội tâm — độc thoại phải chuyển thành lời kể gián tiếp ("hắn thầm nghĩ…") hoặc
  trích dẫn ngắn trong ngoặc kép.
- Xưng hô ngôi nhất/ngôi hai (ta, ngươi, huynh, muội…) **chỉ được nằm BÊN TRONG dấu ngoặc kép**
  của thoại trích dẫn, giữ đúng cách các nhân vật xưng hô với nhau.

### Nhiệm vụ

- Viết lời kể tiếng Việt **MẠCH LẠC**, xâu chuỗi các khung thành một mạch truyện liền, thoại
  lồng vào lời kể dạng trích dẫn ngắn.
- Được **GỘP** các khung liền kề vào MỘT đoạn kể khi chúng cùng một diễn biến.
- Được **TÁCH** một khung dày nội dung thành NHIỀU đoạn kể liên tiếp.
- Mỗi index phải xuất hiện trong **đúng một đoạn** (không bỏ sót, không lặp) — `validate.py`
  fail cứng nếu vi phạm.

### Cách đọc content khi viết (format từng khung, port script.ts:88-98)

Tự dựng trong đầu mỗi slice theo dạng: `[i] description | Thoại: "câu 1" "câu 2" | Lưu ý: note`
— description rỗng đọc là "(không có mô tả)"; `note` mang tín hiệu mạch truyện (chuyển cảnh,
hồi tưởng, chi tiết nền) — vứt đi là mất đúng thứ giúp hiểu kịch bản.

### Ngữ cảnh chương (nếu user cung cấp)

- Dùng để gọi ĐÚNG TÊN nhân vật trong lời kể và chọn đúng cách các nhân vật xưng hô với nhau
  TRONG THOẠI trích dẫn.
- Xưng hô trong ngữ cảnh là cách nhân vật nói VỚI NHAU — **KHÔNG áp vào giọng người kể** (lời
  kể vẫn ngôi thứ ba, gọi nhân vật bằng tên/đại từ ngôi ba).
- **KHÔNG bịa thêm sự kiện** ngoài nội dung các khung.
