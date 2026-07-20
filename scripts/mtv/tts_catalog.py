# Port từ content-extractor/lib/tts-providers/catalog.ts — đồng bộ 2 nơi khi fix bug.
# Catalog giọng Việt CapCut — CHỈ giọng verify được thực nghiệm. resourceId đi kèm từng giọng.

PROVIDER_LABELS = {
    "elevenlabs": "ElevenLabs",
    "capcut": "CapCut (free, tiếng Việt)",
}

CAPCUT_VOICES = [
    {"id": "BV074_streaming", "label": "Cô Gái Hoạt Ngôn", "resource_id": "7102355709945188865"},
    {"id": "BV075_streaming", "label": "Thanh Niên Tự Tin", "resource_id": "7102355803792740865"},
    {"id": "vi_female_huong", "label": "Giọng Nữ Phổ Thông", "resource_id": "7264854897953083905"},
    {"id": "BV421_vivn_streaming", "label": "Nhỏ Ngọt Ngào", "resource_id": "7252594014782755330"},
    {"id": "BV562_streaming", "label": "Mai", "resource_id": "7483736254694035984"},
    {"id": "BV560_streaming", "label": "Alex Đại Đế", "resource_id": "7483736167565758992"},
    {"id": "multi_female_yangguangnv_uranus_bigtts", "label": "Ban Mai", "resource_id": "7637456432522218773"},
    {"id": "multi_female_sisi_uranus_bigtts", "label": "Bản Tin nữ", "resource_id": "7637455857285860629"},
]
