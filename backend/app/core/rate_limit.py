"""
Rate limiting primitives for the Cograph backend.

Provides a RateLimiter protocol with two concrete implementations:
- RedisRateLimiter: cluster-wide rate limiting backed by Redis (production)
- InMemoryRateLimiter: single-process in-memory implementation (dev/tests)

Both expose the same async interface so callers don't know which one is active.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiter(Protocol):
    async def hit(
        self,
        key: str,
        *,
        window_seconds: int,
        limit: int,
    ) -> RateLimitResult:
        """
        Record one hit for `key` in the fixed window of `window_seconds`.

        Returns RateLimitResult where:
        - allowed=True when the request is within the limit
        - remaining = how many more hits are allowed in the current window
        - retry_after_seconds = 0 when allowed, >0 when denied (seconds until window resets)
        """
        ...

    async def check(
        self,
        key: str,
        *,
        window_seconds: int,
        limit: int,
    ) -> RateLimitResult:
        """
        Read-only check: returns the current window state WITHOUT incrementing
        the counter. Used to pre-check the email counter before bcrypt so we
        short-circuit immediately when the limit is already exhausted.
        """
        ...


class RedisRateLimiter:
    """
    Redis-backed rate limiter using an atomic INCR + EXPIRE pipeline.

    Each key is a string like "rate:login:ip:<ip>" and maps to a counter
    that expires after `window_seconds`. The EXPIRE call uses NX so it only
    sets the TTL on the first INCR — subsequent increments within the window
    leave the original TTL untouched.
    """

    def __init__(self, redis) -> None:
        # redis should be a redis.asyncio.Redis client
        self._redis = redis

    async def hit(
        self,
        key: str,
        *,
        window_seconds: int,
        limit: int,
    ) -> RateLimitResult:
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.incr(key)
            await pipe.expire(key, window_seconds, nx=True)
            results = await pipe.execute()

        count: int = results[0]
        if count <= limit:
            return RateLimitResult(allowed=True, remaining=limit - count, retry_after_seconds=0)

        # Fetch the remaining TTL so we can return an accurate Retry-After.
        ttl: int = await self._redis.ttl(key)
        retry_after = max(ttl, 1)  # never return 0 when denied
        return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)

    async def check(
        self,
        key: str,
        *,
        window_seconds: int,
        limit: int,
    ) -> RateLimitResult:
        """Read-only: inspect current count without incrementing."""
        raw = await self._redis.get(key)
        count = int(raw) if raw else 0
        if count < limit:
            return RateLimitResult(allowed=True, remaining=limit - count, retry_after_seconds=0)

        ttl: int = await self._redis.ttl(key)
        retry_after = max(ttl, 1)
        return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)


class InMemoryRateLimiter:
    """
    Single-process in-memory rate limiter for tests and dev mode.

    Not thread-safe (fine for single-process async tests) and not
    cluster-aware. State is lost on restart.

    Implements a fixed window: the window resets at `created_at + window_seconds`.
    """

    def __init__(self) -> None:
        # key -> (count, window_start_timestamp, window_seconds)
        self._windows: dict[str, tuple[int, float, int]] = defaultdict(
            lambda: (0, 0.0, 0)
        )

    def _current_count(self, key: str, window_seconds: int) -> tuple[int, float]:
        """Return (count, window_start) for the current active window."""
        count, window_start, stored_window = self._windows[key]
        now = time.monotonic()
        if count == 0 or stored_window == 0 or (now - window_start) >= stored_window:
            # Window expired or never started.
            return 0, now
        return count, window_start

    async def hit(
        self,
        key: str,
        *,
        window_seconds: int,
        limit: int,
    ) -> RateLimitResult:
        now = time.monotonic()
        count, window_start = self._current_count(key, window_seconds)

        if count == 0:
            window_start = now

        count += 1
        self._windows[key] = (count, window_start, window_seconds)

        if count <= limit:
            return RateLimitResult(allowed=True, remaining=limit - count, retry_after_seconds=0)

        elapsed = now - window_start
        retry_after = max(int(window_seconds - elapsed) + 1, 1)
        return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)

    async def check(
        self,
        key: str,
        *,
        window_seconds: int,
        limit: int,
    ) -> RateLimitResult:
        """Read-only: inspect current count without incrementing."""
        now = time.monotonic()
        count, window_start = self._current_count(key, window_seconds)

        if count < limit:
            return RateLimitResult(allowed=True, remaining=limit - count, retry_after_seconds=0)

        elapsed = now - window_start
        retry_after = max(int(window_seconds - elapsed) + 1, 1)
        return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)
