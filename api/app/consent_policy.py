"""Versioned AI-disclosure catalog shared by config and consent enforcement.

Policy support and policy activation are deliberately separate.  Shipping code that
understands a new disclosure must not strand an already-installed client; the new
policy becomes required only through ``AI_CONSENT_REQUIRED_POLICY_VERSION`` after
the compatible iOS build is available.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsentPolicy:
    version: str
    provider: str
    rank: int
    minimum_ios_build: int


ANTHROPIC_V1 = ConsentPolicy(
    version="anthropic-2026-08-12-v1",
    provider="Anthropic",
    rank=1,
    minimum_ios_build=7,
)
ANTHROPIC_OPENAI_V2 = ConsentPolicy(
    version="anthropic-openai-2026-08-13-v2",
    provider="Anthropic and OpenAI",
    rank=2,
    minimum_ios_build=8,
)

POLICIES = (ANTHROPIC_V1, ANTHROPIC_OPENAI_V2)
POLICIES_BY_VERSION = {policy.version: policy for policy in POLICIES}
LEGACY_POLICY_VERSION = ANTHROPIC_V1.version
LATEST_POLICY_VERSION = ANTHROPIC_OPENAI_V2.version
DEFAULT_REQUIRED_POLICY_VERSION = LEGACY_POLICY_VERSION


def policy_for(version: str) -> ConsentPolicy:
    try:
        return POLICIES_BY_VERSION[version]
    except KeyError as exc:
        raise ValueError(f"unsupported AI consent policy version: {version!r}") from exc


def satisfies(recorded_version: str, required_version: str) -> bool:
    """Return whether one recorded disclosure covers the required provider set."""
    recorded = POLICIES_BY_VERSION.get(recorded_version)
    required = POLICIES_BY_VERSION.get(required_version)
    return recorded is not None and required is not None and recorded.rank >= required.rank
