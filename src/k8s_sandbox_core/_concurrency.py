"""Simple asyncio.Semaphore-based concurrency limiter.

Replaces inspect_ai.util.concurrency for standalone use.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_semaphores: dict[str, asyncio.Semaphore] = {}


@asynccontextmanager
async def concurrency(name: str, count: int) -> AsyncIterator[None]:
    """Acquire a named semaphore, limiting concurrent operations.

    Args:
        name: Semaphore name (used to deduplicate across calls).
        count: Maximum concurrent permits.
    """
    if name not in _semaphores:
        _semaphores[name] = asyncio.Semaphore(count)
    async with _semaphores[name]:
        yield
