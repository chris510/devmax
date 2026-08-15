"""Server-owned, versioned routing for Recall scoring providers.

The route is frozen onto a session when it is created. That keeps every scored
turn in a multi-probe session on the same provider/model contract even if a
deployment variable changes while the session is still resumable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import string
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.config import Settings
from app.services.scheduler import rating_for
from app.services.scoring_contract import SCORING_CONTRACT_V2

ROUTE_ANTHROPIC = "anthropic"
ROUTE_SHADOW = "shadow"
ROUTE_PRIMARY = "primary"
ROUTE_FORMAT_VERSION = 1
QUALIFICATION_FORMAT_VERSION = 1
OPENAI_REQUEST_FORMAT_VERSION = 1
# These versions bind the response interpretation and every downstream product
# branch to the qualification evidence. Bump the relevant value whenever a
# parser rule or Recall-to-product decision changes, even if the wire schema and
# public scoring-contract number stay the same.
V2_PARSER_POLICY_VERSION = 2
PRODUCT_DECISION_POLICY_VERSION = 1
OPENAI_V2_SCHEMA_NAME = "devmax_recall_score_v2"
QUALIFICATION_DYNAMIC_USER_CONTENT = "<dynamic-user-content>"
QUALIFICATION_DYNAMIC_SAFETY_IDENTIFIER = "<dynamic-safety-identifier>"
_ROUTE_KEYS = frozenset(
    {
        "format_version",
        "mode",
        "anthropic_model",
        "anthropic_effort",
        "openai_model",
        "openai_effort",
        "qualification_fingerprint",
    }
)
_OPENAI_EFFORTS = frozenset(("none", "low", "medium", "high", "xhigh", "max"))

RouteMode = Literal["anthropic", "shadow", "primary"]


@dataclass(frozen=True)
class ProviderCallTrace:
    provider: str
    model: str
    response_model: str = ""
    response_id: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    outcome: str = "success"
    error_type: str = ""


@dataclass(frozen=True)
class ShadowComparison:
    authoritative_recall: int | None
    candidate_recall: int | None
    within_one: bool
    behavioral_match: bool
    authoritative_decisions: dict[str, str]
    candidate_decisions: dict[str, str]


@dataclass(frozen=True)
class OpenAIEligibility:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class ScoringTrace:
    route: RouteMode
    authoritative_provider: str
    qualification_fingerprint: str
    calls: tuple[ProviderCallTrace, ...]
    fallback_reason: str = ""
    candidate_error: str = ""
    shadow: ShadowComparison | None = None

    def usage_details(self) -> list[dict[str, Any]]:
        """One privacy-safe durable record per physical provider call."""
        common: dict[str, Any] = {
            "route": self.route,
            "authoritative_provider": self.authoritative_provider,
            "qualification_fingerprint": self.qualification_fingerprint,
            "fallback_reason": self.fallback_reason,
            "candidate_error": self.candidate_error,
        }
        if self.shadow is not None:
            common["shadow"] = asdict(self.shadow)
        return [{**common, "call": asdict(call)} for call in self.calls]


@dataclass(frozen=True)
class ScoringRoute:
    mode: RouteMode
    anthropic_model: str
    anthropic_effort: str | None
    openai_model: str = ""
    openai_effort: str | None = None
    qualification_fingerprint: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"format_version": ROUTE_FORMAT_VERSION, **asdict(self)}

    @classmethod
    def from_json(cls, value: dict[str, Any] | None, settings: Settings) -> ScoringRoute:
        """Read a frozen route; legacy sessions safely remain on Anthropic."""
        if value is None or value == {}:
            return anthropic_route(settings)
        try:
            if not isinstance(value, dict) or set(value) != _ROUTE_KEYS:
                raise ValueError("unexpected scoring route fields")
            if (
                type(value["format_version"]) is not int
                or value["format_version"] != ROUTE_FORMAT_VERSION
            ):
                raise ValueError("unsupported scoring route format")
            mode = value["mode"]
            if mode not in {ROUTE_ANTHROPIC, ROUTE_SHADOW, ROUTE_PRIMARY}:
                raise ValueError("unknown scoring route")
            anthropic_model = _required_text(
                value["anthropic_model"], field="anthropic_model"
            )
            anthropic_effort = _optional_text(
                value["anthropic_effort"], field="anthropic_effort"
            )
            openai_model = _empty_or_text(
                value["openai_model"], field="openai_model"
            )
            openai_effort = _optional_text(
                value["openai_effort"], field="openai_effort"
            )
            fingerprint = _empty_or_text(
                value["qualification_fingerprint"],
                field="qualification_fingerprint",
            )
            if mode == ROUTE_ANTHROPIC:
                if openai_model or openai_effort or fingerprint:
                    raise ValueError("Anthropic route contains OpenAI fields")
            else:
                if not openai_model:
                    raise ValueError("OpenAI route has no model")
                if openai_effort not in _OPENAI_EFFORTS:
                    raise ValueError("OpenAI route has an unsupported effort")
                if (
                    len(fingerprint) != 64
                    or fingerprint != fingerprint.lower()
                    or any(character not in string.hexdigits for character in fingerprint)
                ):
                    raise ValueError("OpenAI route has an invalid qualification fingerprint")
            return cls(
                mode=mode,  # type: ignore[arg-type]
                anthropic_model=anthropic_model,
                anthropic_effort=anthropic_effort,
                openai_model=openai_model,
                openai_effort=openai_effort,
                qualification_fingerprint=fingerprint,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("session contains an invalid frozen scoring route") from exc


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be null or non-empty text")
    return value.strip()


def _empty_or_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value.strip()


def anthropic_route(settings: Settings) -> ScoringRoute:
    return ScoringRoute(
        mode=ROUTE_ANTHROPIC,
        anthropic_model=settings.scoring_model,
        anthropic_effort=settings.scoring_effort,
    )


def qualification_is_current(
    settings: Settings, *, now: datetime | None = None
) -> bool:
    """Fail closed when the deployed qualification deadline is absent or elapsed."""
    try:
        expires_at = settings.openai_v2_scoring_qualification_expiry
    except (AttributeError, ValueError):
        return False
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("qualification clock must be timezone-aware")
    return instant.astimezone(UTC) < expires_at


def route_for_session(
    settings: Settings, *, user_id: uuid.UUID, scoring_contract_version: int
) -> ScoringRoute:
    """Choose a route once, from server configuration and the authenticated user."""
    if (
        scoring_contract_version != SCORING_CONTRACT_V2
        or settings.openai_v2_scoring_mode == "off"
        or user_id not in settings.openai_v2_scoring_user_id_set
        or not qualification_is_current(settings)
    ):
        return anthropic_route(settings)
    return ScoringRoute(
        mode=(
            ROUTE_SHADOW
            if settings.openai_v2_scoring_mode == "shadow"
            else ROUTE_PRIMARY
        ),
        anthropic_model=settings.scoring_model,
        anthropic_effort=settings.scoring_effort,
        openai_model=settings.openai_v2_scoring_model,
        openai_effort=settings.openai_v2_scoring_effort,
        qualification_fingerprint=(
            settings.openai_v2_scoring_qualification_fingerprint.lower()
        ),
    )


def openai_route_eligibility(
    settings: Settings,
    *,
    route: ScoringRoute,
    user_id: uuid.UUID | None,
    actual_fingerprint: str,
) -> OpenAIEligibility:
    """Resolve every mutable deployment gate for one frozen OpenAI route.

    Callers use the returned reason both for Claude-only routing and durable
    intent planning. The same function is called again immediately before a
    physical Responses request, so an open session cannot outlive a revoked or
    expired qualification.
    """
    if route.mode == ROUTE_ANTHROPIC:
        return OpenAIEligibility(False, "anthropic_route")
    if settings.openai_v2_scoring_mode == "off":
        return OpenAIEligibility(False, "kill_switch")
    if not qualification_is_current(settings):
        return OpenAIEligibility(False, "qualification_expired")
    if user_id is None:
        return OpenAIEligibility(False, "authenticated_user_missing")
    if user_id not in settings.openai_v2_scoring_user_id_set:
        return OpenAIEligibility(False, "allowlist_removed")
    if actual_fingerprint != route.qualification_fingerprint:
        return OpenAIEligibility(False, "qualification_fingerprint_mismatch")
    if (
        route.qualification_fingerprint
        != settings.openai_v2_scoring_qualification_fingerprint.lower()
        or route.openai_model != settings.openai_v2_scoring_model
        or route.openai_effort != settings.openai_v2_scoring_effort
    ):
        return OpenAIEligibility(False, "deployed_qualification_changed")
    return OpenAIEligibility(True)


def openai_responses_request(
    completion: Mapping[str, Any],
    *,
    schema_name: str,
    safety_identifier: str | None = None,
) -> dict[str, Any]:
    """Build the one canonical Responses request used by eval and production."""
    request: dict[str, Any] = {
        "model": completion["model"],
        "instructions": completion["rubric"],
        "input": completion["user_content"],
        "max_output_tokens": completion["max_tokens"],
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": completion["schema"],
            }
        },
    }
    if completion.get("effort") is not None:
        request["reasoning"] = {"effort": completion["effort"]}
    if safety_identifier:
        request["safety_identifier"] = safety_identifier
    return request


def qualification_request(completion: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical request shape bound by qualification.

    Learner content and the account-specific safety pseudonym are dynamic, but
    their field names and placement are part of the qualified wire contract.
    """
    canonical_completion = {
        **completion,
        "user_content": QUALIFICATION_DYNAMIC_USER_CONTENT,
    }
    return openai_responses_request(
        canonical_completion,
        schema_name=OPENAI_V2_SCHEMA_NAME,
        safety_identifier=QUALIFICATION_DYNAMIC_SAFETY_IDENTIFIER,
    )


def qualification_fingerprint(completion: dict[str, Any]) -> str:
    """Fingerprint the exact static request contract that passed qualification."""
    payload = {
        "format_version": QUALIFICATION_FORMAT_VERSION,
        "scoring_contract_version": SCORING_CONTRACT_V2,
        "parser_policy_version": V2_PARSER_POLICY_VERSION,
        "product_decision_policy_version": PRODUCT_DECISION_POLICY_VERSION,
        "endpoint": "/v1/responses",
        "request_format_version": OPENAI_REQUEST_FORMAT_VERSION,
        "request": qualification_request(completion),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def safety_identifier(settings: Settings, user_id: uuid.UUID) -> str:
    """Stable pseudonymous identifier required by current OpenAI guidance."""
    return hmac.new(
        settings.openai_safety_identifier_secret.encode(),
        user_id.bytes,
        hashlib.sha256,
    ).hexdigest()


def product_decisions(*, status: str, recall: int | None) -> dict[str, str]:
    """Every current product branch a Recall result can change.

    Initial follow-ups have no displayed/scheduled score yet, so flow is their
    only product decision. Completed results additionally branch the scheduler,
    Today's four-band summary, and Coverage's five-tier vocabulary.
    """
    decisions = {"flow": status}
    if status != "complete" or recall is None:
        return decisions
    decisions["scheduler"] = rating_for(recall)
    decisions["today_band"] = (
        "cold" if recall <= 1 else "shaky" if recall <= 3 else "solid"
    )
    decisions["coverage_tier"] = (
        "cold"
        if recall <= 1
        else "shaky"
        if recall == 2
        else "developing"
        if recall == 3
        else "solid"
    )
    return decisions


def compare_shadow_results(
    *,
    authoritative_status: str,
    authoritative_recall: int | None,
    candidate_status: str,
    candidate_recall: int | None,
) -> ShadowComparison:
    authoritative = product_decisions(
        status=authoritative_status, recall=authoritative_recall
    )
    candidate = product_decisions(status=candidate_status, recall=candidate_recall)
    within_one = (
        authoritative_recall is not None
        and candidate_recall is not None
        and abs(authoritative_recall - candidate_recall) <= 1
    )
    return ShadowComparison(
        authoritative_recall=authoritative_recall,
        candidate_recall=candidate_recall,
        within_one=within_one,
        behavioral_match=authoritative == candidate,
        authoritative_decisions=authoritative,
        candidate_decisions=candidate,
    )
