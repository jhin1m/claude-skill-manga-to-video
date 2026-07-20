# Helper số học khớp semantics JavaScript — dùng chung cho các module port từ TS.
# Math.round của JS làm tròn half-away-from-zero cho số dương (round(0.5)=1, round(2.5)=3),
# khác round() Python (banker's rounding) — sai 1px là lệch parity.

from __future__ import annotations

import math


def js_round(x: float) -> int:
    """JS Math.round: floor(x + 0.5) — đúng cho cả số âm (round(-0.5) = 0)."""
    return math.floor(x + 0.5)
