#!/usr/bin/env python
"""Compare scoring quality and token spend across `effort` levels.

Not a test — this makes live Anthropic calls and costs real money (roughly
$0.013 per case per level at Sonnet 5 intro pricing). It exists to answer one
question: does `scoring_effort="low"` grade the same as `"medium"`? Thinking
tokens are billed as output and dominate the bill, so that setting is the only
meaningful cost lever in the app.

    uv run python scripts/effort_sweep.py scripts/effort_cases.json
    uv run python scripts/effort_sweep.py cases.json --levels low medium high

Token counts are read off the `log.info` line that `_complete` already emits,
so measurement needs no production code change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import llm  # noqa: E402

DEFAULT_LEVELS = ("low", "medium")


class _UsageTap(logging.Handler):
    """Accumulates output tokens from `llm`'s own "llm model=..." log record.

    Coupled to the argument order in `_complete`'s log.info call
    (model, attempt, ms, in, out, cache_read, cache_write). If that line
    changes, this breaks loudly rather than reporting silently wrong numbers.
    """

    def __init__(self) -> None:
        super().__init__()
        self.output_tokens = 0

    def emit(self, record: logging.LogRecord) -> None:
        if not record.getMessage().startswith("llm model="):
            return
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            raise RuntimeError(f"unexpected llm log shape: {args!r} — update _UsageTap")
        self.output_tokens += int(args[4])


@dataclass
class Result:
    case: str
    expected: int | None
    score: int
    output_tokens: int
    feedback: str = ""


async def run_case(case: dict, tap: _UsageTap) -> Result:
    tap.output_tokens = 0
    result = await llm.score_answer(
        topic=case["topic"],
        mastery_summary=case.get("mastery_summary", ""),
        question_asked=case["question"],
        answer_text=case["answer"],
        follow_up_question=None,
        follow_up_answer="",
        # Forced True so every case returns a score rather than a probe — we are
        # measuring grading, and a 2 or 3 would otherwise short-circuit to a
        # follow-up and never produce a comparable number.
        follow_up_used=True,
    )
    return Result(
        case=case.get("name", case["topic"]),
        expected=case.get("expected_score"),
        score=result.score if result.score is not None else -1,
        output_tokens=tap.output_tokens,
        feedback=result.feedback,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="JSON file: a list of case objects")
    parser.add_argument("--levels", nargs="+", default=list(DEFAULT_LEVELS))
    parser.add_argument("--verbose", action="store_true", help="print feedback per case")
    args = parser.parse_args()

    # Python block-buffers stdout when piped, which hides per-case progress for
    # the several minutes a full sweep takes. Line buffering makes it watchable.
    sys.stdout.reconfigure(line_buffering=True)

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is unset — this script makes live calls.", file=sys.stderr)
        return 1

    cases = json.loads(args.cases.read_text())
    tap = _UsageTap()
    logging.getLogger("app.services.llm").addHandler(tap)
    logging.getLogger("app.services.llm").setLevel(logging.INFO)

    original_effort = settings.scoring_effort
    by_level: dict[str, list[Result]] = {}
    try:
        for level in args.levels:
            settings.scoring_effort = level
            results = by_level.setdefault(level, [])
            print(f"\n=== effort={level} (model={settings.scoring_model}) ===")
            for case in cases:
                result = await run_case(case, tap)
                results.append(result)
                expected = "—" if result.expected is None else str(result.expected)
                flag = ""
                if result.expected is not None and result.score != result.expected:
                    flag = f"  <-- off by {abs(result.score - result.expected)}"
                print(
                    f"  {result.case[:44]:<44} "
                    f"score={result.score} expected={expected} "
                    f"out={result.output_tokens:>5}{flag}"
                )
                if args.verbose:
                    print(f"      {result.feedback}")
    finally:
        settings.scoring_effort = original_effort
        logging.getLogger("app.services.llm").removeHandler(tap)

    def deviations(results: list[Result]) -> list[int]:
        return [abs(r.score - r.expected) for r in results if r.expected is not None]

    def total_output(results: list[Result]) -> int:
        return sum(r.output_tokens for r in results)

    print("\n=== summary ===")
    print(f"  {'level':<10} {'out tok':>9} {'mean dev':>9} {'max dev':>8} {'exact':>8}")
    for level, results in by_level.items():
        devs = deviations(results)
        mean_dev = f"{statistics.mean(devs):.2f}" if devs else "—"
        max_dev = str(max(devs)) if devs else "—"
        exact = f"{sum(1 for d in devs if d == 0)}/{len(devs)}" if devs else "—"
        print(f"  {level:<10} {total_output(results):>9} {mean_dev:>9} {max_dev:>8} {exact:>8}")

    if len(by_level) > 1:
        (base_level, base_results), *rest = by_level.items()
        base_total = total_output(base_results)
        print(
            f"\n  baseline: {base_level}. Adopt a cheaper level only if its deviation "
            f"column matches and it saves real tokens."
        )
        for level, results in rest:
            if base_total:
                delta = (total_output(results) - base_total) / base_total * 100
                print(f"    {level}: {delta:+.0f}% output tokens vs {base_level}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
