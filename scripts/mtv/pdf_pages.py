# Port từ content-extractor/lib/pdf.ts — đồng bộ 2 nơi khi fix bug.
# Render PDF → PNG từng trang. TS dùng pdf-to-img (pdfjs); Python dùng pymupdf —
# raster khác biệt chấp nhận được (PDF không nằm trong parity gate banding).
# Scale 1 = 72dpi; 2 (mặc định) ≈ 144dpi đủ nét để downscale MAX_EDGE không mất chữ.

from __future__ import annotations

import fitz  # pymupdf

PDF_SCALE_DEFAULT = 2


def render_pdf(data: bytes, scale: float = PDF_SCALE_DEFAULT) -> list[bytes]:
    """PDF bytes → list PNG bytes từng trang. Fail rõ kèm số trang lỗi."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as err:
        raise RuntimeError(f"Không đọc được file PDF: {err}") from err
    pages: list[bytes] = []
    matrix = fitz.Matrix(scale, scale)
    for i in range(doc.page_count):
        # Render từng trang riêng để lỗi chỉ đúng trang hỏng — thà fail rõ ràng
        # còn hơn lặng lẽ mất trang giữa chapter (triết lý ingest pdf.ts:22-29)
        try:
            pix = doc.load_page(i).get_pixmap(matrix=matrix)
            pages.append(pix.tobytes("png"))
        except Exception as err:
            raise RuntimeError(f"Không render được trang {i + 1}/{doc.page_count} của PDF: {err}") from err
    return pages
