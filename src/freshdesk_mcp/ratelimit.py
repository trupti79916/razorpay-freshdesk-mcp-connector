"""Proactive + reactive rate-limit handling for the Freshdesk API."""
from __future__ import annotations

import asyncio
import random
import time
from typing import Mapping


class RateLimitedError(Exception):
    """Raised when retries are exhausted on HTTP 429."""

    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Freshdesk rate limit hit; retry after ~{retry_after_seconds:.0f}s")


class RateLimiter:
    """Token bucket (requests/minute) + concurrency cap + header-driven slowdown."""

    def __init__(self, per_minute: int = 40, max_concurrency: int = 3):
        self.capacity = max(1, per_minute)
        self.tokens = float(self.capacity)
        self.refill_per_sec = self.capacity / 60.0
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.remaining: int | None = None  # from X-Ratelimit-Remaining
        self.total: int | None = None  # from X-Ratelimit-Total
        self.last_429_at: float | None = None

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self._updated) * self.refill_per_sec)
        self._updated = now

    async def acquire(self) -> None:
        """Wait until a token is available (proactive throttling)."""
        while True:
            async with self._lock:
                self._refill()
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.refill_per_sec
            await asyncio.sleep(wait)

    def observe(self, headers: Mapping[str, str]) -> None:
        """Record server-reported budget; drain our bucket if the server says we're nearly out."""
        try:
            if "x-ratelimit-remaining" in headers:
                self.remaining = int(headers["x-ratelimit-remaining"])
            if "x-ratelimit-total" in headers:
                self.total = int(headers["x-ratelimit-total"])
        except ValueError:
            return
        if self.remaining is not None and self.remaining <= 3:
            self.tokens = min(self.tokens, 0.0)

    def note_429(self) -> None:
        self.last_429_at = time.monotonic()
        self.tokens = min(self.tokens, 0.0)

    def snapshot(self) -> dict:
        self._refill()
        return {
            "server_remaining": self.remaining,
            "server_total": self.total,
            "local_tokens": round(self.tokens, 1),
            "local_capacity_per_min": self.capacity,
        }


def backoff_seconds(attempt: int, retry_after: str | None) -> float:
    """Honour Retry-After, else exponential backoff with jitter."""
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return min(30.0, (2**attempt) + random.uniform(0, 0.5))
