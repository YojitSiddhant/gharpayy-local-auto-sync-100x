"""Bounded OCR concurrency shared by upload, screen-capture frames and Retry OCR."""
import asyncio
from collections.abc import Callable
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

from backend.app.core.config import Settings
from backend.app.core.errors import ApiError

T = TypeVar("T")
_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0


def _slots(size: int) -> asyncio.Semaphore:
    global _semaphore, _semaphore_size
    if _semaphore is None or _semaphore_size != size:
        _semaphore, _semaphore_size = asyncio.Semaphore(max(1, size)), size
    return _semaphore


async def run_ocr_bounded(settings: Settings, fn: Callable[..., T], *args) -> T:
    slots = _slots(settings.ocr_max_concurrency)
    try:
        await asyncio.wait_for(slots.acquire(), timeout=settings.ocr_queue_wait_seconds)
    except TimeoutError:
        raise ApiError(429, "OCR_BUSY", "OCR is busy with another screenshot. Try again shortly.") from None
    try:
        return await run_in_threadpool(fn, *args)
    finally:
        slots.release()
