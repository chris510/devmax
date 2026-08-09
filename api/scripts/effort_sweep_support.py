"""Shared concurrency and per-call usage support for paid model sweeps."""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import logging
import math
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO

from app.services import llm
from app.services.card_lifecycle import Grounding, GroundingError

RESULT_FORMAT_VERSION = 1
USD_DISPLAY_QUANTUM = Decimal("0.0001")

# Versioned from the providers' public pricing tables. Sonnet 5's introductory
# rate ends after 2026-08-31; keeping the boundary in code prevents a stale
# promotion from silently approving an under-budget run.
# https://platform.claude.com/docs/en/about-claude/pricing
# https://developers.openai.com/api/docs/pricing
MODEL_RATES: dict[str, tuple[tuple[date, Decimal, Decimal], ...]] = {
    "claude-sonnet-5": (
        (date(2026, 8, 31), Decimal("2"), Decimal("10")),
        (date.max, Decimal("3"), Decimal("15")),
    ),
    "claude-sonnet-4-6": ((date.max, Decimal("3"), Decimal("15")),),
    "claude-sonnet-4-5": ((date.max, Decimal("3"), Decimal("15")),),
    "claude-haiku-4-5": ((date.max, Decimal("1"), Decimal("5")),),
    "claude-opus-5": ((date.max, Decimal("5"), Decimal("25")),),
    "gpt-5.6-luna": ((date.max, Decimal("0.2"), Decimal("1.2")),),
    "gpt-5.6-terra": ((date.max, Decimal("2"), Decimal("12")),),
}

BATCH_MODEL_RATES: dict[str, tuple[tuple[date, Decimal, Decimal], ...]] = {
    "gpt-5.6-luna": ((date.max, Decimal("0.1"), Decimal("0.6")),),
    "gpt-5.6-terra": ((date.max, Decimal("1"), Decimal("6")),),
}

case_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "effort_sweep_case", default=None
)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class PreparedCall:
    index: int
    case: dict[str, Any]
    case_name: str
    kind: str
    effort: str | None
    completion: dict[str, Any]
    fingerprint: str
    scoring_prompt_variant: str = "production"


@dataclass(frozen=True)
class ModelRate:
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    label: str


@dataclass(frozen=True)
class CostEstimate:
    calls: int
    input_tokens: int
    output_tokens: int
    usd: Decimal
    output_tokens_per_call: int


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


def case_name(case: dict[str, Any], index: int) -> str:
    return str(case.get("name") or case.get("topic") or f"case-{index + 1}")


def select_cases(
    cases: list[dict],
    *,
    names: list[str] | None,
    tags: list[str] | None,
    parser,
) -> list[dict]:
    """Apply exact-name and tag filters before grounding or any API request."""
    requested_names = set(names or [])
    requested_tags = set(tags or [])
    names_in_order = [case_name(case, index) for index, case in enumerate(cases)]
    duplicates = sorted(
        name for name, count in Counter(names_in_order).items() if count > 1
    )
    if duplicates:
        parser.error(f"case names must be unique: {', '.join(duplicates)}")
    available_names = set(names_in_order)
    missing_names = requested_names - available_names
    if missing_names:
        parser.error(f"unknown case name(s): {', '.join(sorted(missing_names))}")

    available_tags: set[str] = set()
    normalized_tags: list[set[str]] = []
    for index, case in enumerate(cases):
        raw_tags = case.get("tags", [])
        if not isinstance(raw_tags, list) or not all(isinstance(tag, str) for tag in raw_tags):
            parser.error(f"{case_name(case, index)}: tags must be a list of strings")
        case_tags = set(raw_tags)
        normalized_tags.append(case_tags)
        available_tags.update(case_tags)
    missing_tags = requested_tags - available_tags
    if missing_tags:
        parser.error(f"unknown tag(s): {', '.join(sorted(missing_tags))}")

    selected = [
        case
        for index, case in enumerate(cases)
        if (not requested_names or case_name(case, index) in requested_names)
        and (not requested_tags or bool(normalized_tags[index] & requested_tags))
    ]
    if not selected:
        parser.error("case filters selected no cases")
    return selected


def levels_for(explicit: list[str] | None, configured: str | None) -> list[str | None]:
    """Default to the shipping effort; comparisons must be requested explicitly."""
    requested = list(explicit) if explicit else [configured]
    return list(dict.fromkeys(requested))


def completion_fingerprint(
    kind: str, case: dict[str, Any], completion: dict[str, Any]
) -> str:
    """Hash every input that can change the request or its expected judgement."""
    metadata_keys = {"tags", "review_status", "review_note"}
    judged_case = {
        key: value for key, value in case.items() if key not in metadata_keys
    }
    payload = {
        "format_version": RESULT_FORMAT_VERSION,
        "kind": kind,
        "case": judged_case,
        "completion": completion,
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def prepare_call(
    *,
    index: int,
    case: dict[str, Any],
    kind: str,
    effort: str | None,
    completion: dict[str, Any],
    scoring_prompt_variant: str = "production",
) -> PreparedCall:
    return PreparedCall(
        index=index,
        case=case,
        case_name=case_name(case, index),
        kind=kind,
        effort=effort,
        completion=completion,
        fingerprint=completion_fingerprint(kind, case, completion),
        scoring_prompt_variant=scoring_prompt_variant,
    )


def load_result_records(path: Path | None, *, kind: str) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: record must be an object")
            if record.get("format_version") != RESULT_FORMAT_VERSION:
                raise ValueError(f"{path}:{line_number}: unsupported result format")
            if record.get("kind") != kind:
                continue
            fingerprint = record.get("fingerprint")
            if not isinstance(fingerprint, str):
                raise ValueError(f"{path}:{line_number}: missing fingerprint")
            if fingerprint in records:
                raise ValueError(f"{path}:{line_number}: duplicate fingerprint")
            records[fingerprint] = record
    return records


def default_output_path(kind: str, *, now: datetime | None = None) -> Path:
    instant = now or datetime.now(UTC)
    stamp = instant.strftime("%Y%m%dT%H%M%S.%fZ")
    return Path(".eval-results") / f"{kind}-{stamp}.jsonl"


class JsonlRecorder:
    """Flush each paid result immediately so a later failure cannot erase spend."""

    def __init__(self, path: Path):
        self.path = path
        self._file: TextIO | None = None

    def __enter__(self) -> JsonlRecorder:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", encoding="utf-8")
        return self

    def append(self, record: dict[str, Any]) -> None:
        if self._file is None:
            raise RuntimeError("result recorder is not open")
        self._file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._file.flush()

    def __exit__(self, *_args) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def make_result_record(
    prepared: PreparedCall,
    *,
    model: str,
    result: dict[str, Any],
    usage: Usage,
) -> dict[str, Any]:
    return {
        "format_version": RESULT_FORMAT_VERSION,
        "kind": prepared.kind,
        "fingerprint": prepared.fingerprint,
        "created_at": datetime.now(UTC).isoformat(),
        "case": prepared.case_name,
        "model": model,
        "effort": prepared.effort,
        "scoring_prompt_variant": prepared.scoring_prompt_variant,
        "result": result,
        "usage": asdict(usage),
    }


def usage_from_record(record: dict[str, Any]) -> Usage:
    try:
        return Usage(**record["usage"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"invalid usage for {record.get('case', 'unknown case')}") from exc


def positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def add_paid_evaluation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--levels",
        nargs="+",
        default=None,
        help="effort levels; defaults to the configured shipping effort",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--case", action="append", dest="case_names", help="exact case name; repeatable"
    )
    parser.add_argument(
        "--tag", action="append", dest="tags", help="select cases with any tag; repeatable"
    )
    parser.add_argument("--resume", type=Path, help="reuse exact fingerprint matches from JSONL")
    parser.add_argument("--fresh", action="store_true", help="ignore --resume matches")
    parser.add_argument("--output", type=Path, help="new JSONL result path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count tokens and estimate cost without paid Message calls",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=positive_decimal,
        help="required acknowledgement for a paid run; refuses estimates above it",
    )
    parser.add_argument("--input-price-per-million", type=positive_decimal)
    parser.add_argument("--output-price-per-million", type=positive_decimal)


def output_path_for(
    *,
    requested: Path | None,
    resume: Path | None,
    kind: str,
    parser: argparse.ArgumentParser,
) -> Path:
    output = requested or default_output_path(kind)
    if output.exists():
        parser.error(f"output already exists: {output}")
    if resume is not None and output.resolve() == resume.resolve():
        parser.error("--output must differ from --resume")
    return output


def print_preflight(
    estimate: CostEstimate,
    *,
    selected: int,
    resumed: int,
    rate: ModelRate,
    input_label: str = "counted input",
) -> None:
    print("\n=== paid-call preflight ===")
    print(f"  selected calls     {selected}")
    print(f"  resumed calls      {resumed}")
    print(f"  new paid calls     {estimate.calls}")
    print(f"  {input_label:<18} {estimate.input_tokens}")
    print(
        f"  estimated output   {estimate.output_tokens} "
        f"({estimate.output_tokens_per_call}/call)"
    )
    print(
        f"  price / MTok       ${rate.input_per_million} input · "
        f"${rate.output_per_million} output"
    )
    print(f"  pricing schedule   {rate.label}")
    print(f"  estimated ceiling  ${cost_ceiling_for_display(estimate.usd):.4f}")


def cost_ceiling_for_display(cost: Decimal) -> Decimal:
    """Round a preflight estimate upward so the printed budget can authorize it."""
    return cost.quantize(USD_DISPLAY_QUANTUM, rounding=ROUND_CEILING)


def enforce_budget(
    estimate: CostEstimate,
    *,
    budget: Decimal | None,
    dry_run: bool,
    parser: argparse.ArgumentParser,
) -> None:
    if dry_run or estimate.calls == 0:
        return
    if budget is None:
        parser.error(
            "paid run not started: review the estimate, then pass --max-cost-usd"
        )
    if estimate.usd > budget:
        parser.error(
            f"estimated ceiling ${cost_ceiling_for_display(estimate.usd):.4f} "
            f"exceeds --max-cost-usd ${budget}"
        )


def rate_for_model(
    model: str,
    *,
    on_date: date | None = None,
    input_override: Decimal | None = None,
    output_override: Decimal | None = None,
) -> ModelRate:
    if (input_override is None) != (output_override is None):
        raise ValueError("input and output price overrides must be supplied together")
    if input_override is not None and output_override is not None:
        return ModelRate(model, input_override, output_override, "explicit override")

    schedule = MODEL_RATES.get(model)
    if schedule is None:
        raise ValueError(
            f"no price schedule for {model}; pass both per-million price overrides"
        )
    effective = on_date or date.today()
    for through, input_price, output_price in schedule:
        if effective <= through:
            label = (
                "published standard rate"
                if through == date.max
                else f"published schedule through {through.isoformat()}"
            )
            return ModelRate(
                model,
                input_price,
                output_price,
                label,
            )
    raise AssertionError(f"price schedule for {model} has no terminal rate")


def batch_rate_for_model(
    model: str,
    *,
    on_date: date | None = None,
    input_override: Decimal | None = None,
    output_override: Decimal | None = None,
) -> ModelRate:
    """Return the published asynchronous Batch price for an OpenAI model."""
    if (input_override is None) != (output_override is None):
        raise ValueError("input and output price overrides must be supplied together")
    if input_override is not None and output_override is not None:
        return ModelRate(model, input_override, output_override, "explicit Batch override")

    schedule = BATCH_MODEL_RATES.get(model)
    if schedule is None:
        raise ValueError(
            f"no Batch price schedule for {model}; pass both per-million price overrides"
        )
    effective = on_date or date.today()
    for through, input_price, output_price in schedule:
        if effective <= through:
            return ModelRate(
                model,
                input_price,
                output_price,
                "published Batch rate",
            )
    raise AssertionError(f"Batch price schedule for {model} has no terminal rate")


def pending_case_reviews(cases: list[dict[str, Any]]) -> list[str]:
    """List explicit review candidates while preserving trusted legacy packs."""
    return [
        case_name(case, index)
        for index, case in enumerate(cases)
        if case.get("review_status", "approved") != "approved"
    ]


async def count_prepared_calls(
    prepared: list[PreparedCall],
    *,
    concurrency: int,
    client=None,
) -> dict[str, int]:
    """Count exact inputs through Anthropic's free endpoint, with no Message calls."""
    token_client = client or llm._client()
    semaphore = asyncio.Semaphore(concurrency)

    async def count(call: PreparedCall) -> tuple[str, int]:
        async with semaphore:
            result = await token_client.messages.count_tokens(
                **llm.count_params_for_completion(call.completion)
            )
        return call.fingerprint, int(result.input_tokens)

    pairs = await asyncio.gather(*(count(call) for call in prepared))
    return dict(pairs)


def _percentile_95(values: list[int], fallback: int) -> int:
    if not values:
        return fallback
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def estimate_cost(
    prepared: list[PreparedCall],
    *,
    input_counts: dict[str, int],
    prior_records: list[dict[str, Any]],
    fallback_output_tokens: int,
    rate: ModelRate,
) -> CostEstimate:
    prior_outputs = [
        int(record.get("usage", {}).get("output_tokens", 0))
        for record in prior_records
        if record.get("model") == rate.model
        and record.get("effort") in {call.effort for call in prepared}
    ]
    output_per_call = _percentile_95(prior_outputs, fallback_output_tokens)
    input_tokens = sum(input_counts[call.fingerprint] for call in prepared)
    output_tokens = output_per_call * len(prepared)
    usd = (
        Decimal(input_tokens) * rate.input_per_million
        + Decimal(output_tokens) * rate.output_per_million
    ) / Decimal(1_000_000)
    return CostEstimate(
        calls=len(prepared),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=usd,
        output_tokens_per_call=output_per_call,
    )


def actual_cost(records: list[dict[str, Any]], rate: ModelRate) -> Decimal:
    input_tokens = sum(int(record["usage"]["input_tokens"]) for record in records)
    output_tokens = sum(int(record["usage"]["output_tokens"]) for record in records)
    return (
        Decimal(input_tokens) * rate.input_per_million
        + Decimal(output_tokens) * rate.output_per_million
    ) / Decimal(1_000_000)


def hydrate_grounding(cases: list[dict], manifest_path: Path, parser) -> list[dict]:
    """Attach approved answer authority before a paid evaluation can start.

    Cases intentionally contain only the learner answer and expected labels.
    The reviewed curriculum manifest owns the question, basis, and rubric, so
    evaluation cannot silently drift from what production will score.
    """
    entries = load_cases(manifest_path, parser)
    by_topic: dict[str, dict] = {}
    for entry in entries:
        topic = entry.get("topic")
        if not topic:
            parser.error("every grounding manifest entry must have a topic")
        if topic in by_topic:
            parser.error(f"grounding manifest has duplicate topic: {topic}")
        by_topic[topic] = entry

    hydrated: list[dict] = []
    for case in cases:
        topic = case.get("topic")
        entry = by_topic.get(topic)
        if entry is None:
            parser.error(f"no grounding manifest entry for evaluation topic: {topic}")
        status = entry.get("grounding_status", "missing")
        if status != "approved":
            parser.error(
                f"{topic}: grounding status is {status!r}; human approval is required "
                "before live evaluation"
            )
        grounding = Grounding(
            source_url=entry.get("source_url", ""),
            source_section=entry.get("source_section", ""),
            source_label=entry.get("source_label", ""),
            answer_basis=entry.get("answer_basis", ""),
            answer_rubric=entry.get("answer_rubric"),
            canonical_question=entry.get("canonical_question", ""),
        )
        try:
            authority = grounding.require_complete()
        except GroundingError as exc:
            parser.error(
                f"{topic}: approved grounding is incomplete ({', '.join(exc.missing)})"
            )
        hydrated.append(
            {
                **case,
                "question": authority.canonical_question,
                "answer_basis": authority.answer_basis,
                "answer_rubric": authority.answer_rubric,
            }
        )
    return hydrated


async def run_bounded[T, U](
    items: list[U], concurrency: int, call: Callable[[int, U], Awaitable[T]]
) -> list[T]:
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(index: int, item: U) -> T:
        async with semaphore:
            return await call(index, item)

    # gather preserves source order even when calls complete out of order.
    return await asyncio.gather(*(bounded(index, item) for index, item in enumerate(items)))
