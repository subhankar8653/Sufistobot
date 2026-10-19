#@suhanibots
"""
Small, dependency-free helpers that keep a worker bot alive under bursts
(e.g. 100 people tapping the same link within a second) on a small server.

  RateLimiter       smooth "N calls per second" gate shared by all users of a bot
  SingleFlightCache one fetch for many identical concurrent requests (+ short TTL)
  LogBatcher        merges many log lines into few messages (channel flood limit
                    is ~20 messages / minute)
"""

import asyncio
import time
from collections import deque

from config import LOGGER

log = LOGGER(__name__)


class RateLimiter:
    """Smoothly spaces calls so at most `rate` happen per second."""

    def __init__(self, rate: float = 20.0):
        self.interval = 1.0 / max(rate, 0.1)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                self._next += self.interval
            else:
                self._next = now + self.interval
        if wait > 0:
            await asyncio.sleep(wait)


class SingleFlightCache:
    """get(key, fetcher): concurrent callers with the same key share ONE fetch;
    successful results are kept for `ttl` seconds."""

    def __init__(self, ttl: float = 30.0, max_items: int = 256):
        self.ttl = ttl
        self.max_items = max_items
        self._data: dict = {}
        self._inflight: dict = {}

    async def get(self, key, fetcher, ttl: float = None, cache_if=None):
        ttl = self.ttl if ttl is None else ttl
        now = time.monotonic()
        hit = self._data.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]

        fut = self._inflight.get(key)
        if fut is not None:
            return await asyncio.shield(fut)

        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            value = await fetcher()
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e)
                fut.exception()  # mark as retrieved (no "never retrieved" warning)
            raise
        else:
            if not fut.done():
                fut.set_result(value)
            if cache_if is None or cache_if(value):
                if len(self._data) >= self.max_items:
                    oldest = min(self._data, key=lambda k: self._data[k][0])
                    self._data.pop(oldest, None)
                self._data[key] = (time.monotonic(), value)
            return value
        finally:
            self._inflight.pop(key, None)

    def clear(self):
        self._data.clear()


class LogBatcher:
    """add(text) is instant and never blocks the caller. Lines are sent in the
    background, up to `per_message` lines per message, `interval` seconds apart."""

    def __init__(self, send, interval: float = 4.0, per_message: int = 12, max_queue: int = 2000):
        self._send = send            # async (text) -> None
        self.interval = interval
        self.per_message = per_message
        self.max_queue = max_queue
        self._q: deque = deque()
        self._task = None

    def add(self, text: str):
        if len(self._q) >= self.max_queue:
            return  # drop logs rather than grow memory
        self._q.append(text)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self):
        while self._q:
            chunk = []
            size = 0
            while self._q and len(chunk) < self.per_message and size < 3500:
                item = self._q.popleft()
                chunk.append(item)
                size += len(item)
            try:
                await self._send("\n\n".join(chunk))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"LogBatcher send failed: {e}")
            if self._q:
                await asyncio.sleep(self.interval)

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
