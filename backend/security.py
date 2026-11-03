from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user_id: str, bucket: str, limit: int, window_seconds: int, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        key = (user_id, bucket)
        cutoff = current - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False, max(1, int(events[0] + window_seconds - current + 0.999))
            events.append(current)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowRateLimiter()


def enforce_rate_limit(user_id: str, bucket: str, limit: int, window_seconds: int) -> None:
    allowed, retry_after = rate_limiter.allow(user_id, bucket, limit, window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
