# Port từ content-extractor/lib/vision.ts (isRetryable :93-103, withRetry :106-119)
# — đồng bộ 2 nơi khi fix bug. Retry 429/5xx/lỗi mạng với backoff mũ; lỗi tự đánh dấu
# retryable=False (vd CapCut ret!=0 shark block) → fail nhanh, không đập lại device đã chặn.

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class HttpStatusError(Exception):
    """Lỗi HTTP có status — withRetry phân loại 429/5xx."""

    def __init__(self, message: str, status: int | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _is_retryable(err: Exception) -> bool:
    retryable = getattr(err, "retryable", None)
    if retryable is False:
        return False
    status = getattr(err, "status", None)
    if status == 429:
        return True
    if status is not None and status >= 500:
        return True
    return status is None  # không có HTTP status → lỗi mạng/timeout


def with_retry(fn: Callable[[], T], tries: int = 4) -> T:
    last_err: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as err:
            last_err = err
            if not _is_retryable(err) or attempt == tries - 1:
                raise
            time.sleep((500 * 2**attempt + random.random() * 250) / 1000)
    raise last_err  # unreachable
