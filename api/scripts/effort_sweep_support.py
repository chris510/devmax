"""Shared concurrency and per-call usage support for paid model sweeps."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

case_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "effort_sweep_case", default=None
)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class UsageTap(logging.Handler):
    """Assign LLM usage logs to the concurrent case that emitted them."""

    def __init__(self) -> None:
        super().__init__()
        self.by_case: dict[str, Usage] = {}

    def start(self, key: str) -> None:
        self.by_case[key] = Usage()

    def usage_for(self, key: str) -> Usage:
        return self.by_case.pop(key)

    def emit(self, record: logging.LogRecord) -> None:
        if not record.getMessage().startswith("llm model="):
            return
        key = case_key.get()
        if key is None:
            return
        args = record.args
        if not isinstance(args, tuple) or len(args) < 7:
            raise RuntimeError(f"unexpected llm log shape: {args!r} — update UsageTap")
        usage = self.by_case[key]
        usage.input_tokens += int(args[3])
        usage.output_tokens += int(args[4])
        usage.cache_read_tokens += int(args[5])
        usage.cache_write_tokens += int(args[6])


@contextmanager
def capture_usage():
    tap = UsageTap()
    logger = logging.getLogger("app.services.llm")
    logger.addHandler(tap)
    logger.setLevel(logging.INFO)
    try:
        yield tap
    finally:
        logger.removeHandler(tap)


def load_cases(path: Path, parser) -> list[dict]:
    cases = json.loads(path.read_text())
    if not isinstance(cases, list):
        parser.error("cases file must contain a JSON list")
    return cases


async def run_bounded[T](
    cases: list[dict], concurrency: int, call: Callable[[int, dict], Awaitable[T]]
) -> list[T]:
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(index: int, case: dict) -> T:
        async with semaphore:
            return await call(index, case)

    # gather preserves source order even when calls complete out of order.
    return await asyncio.gather(*(bounded(index, case) for index, case in enumerate(cases)))
