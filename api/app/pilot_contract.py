"""Frozen adaptive-study pilot constants shared by runtime and operator tools."""

from datetime import UTC, datetime, timedelta

PILOT_MINIMUM_CLIENT_BUILD = 10
PILOT_RESEARCH_CONSENT_VERSION = "adaptive-study-pilot-research-v1"
PILOT_ASSIGNMENT_ALGORITHM_VERSION = "paired-hmac-sha256-v1"
PILOT_CONSENT_FUTURE_SKEW = timedelta(minutes=5)
PILOT_RESEARCH_CONSENT_CATALOG = {
    PILOT_RESEARCH_CONSENT_VERSION: {
        "minimum_client_build": PILOT_MINIMUM_CLIENT_BUILD,
        "assignment_algorithm": PILOT_ASSIGNMENT_ALGORITHM_VERSION,
    }
}
RESTUDY_PROMPT_VERSION = "source-restudy-v1"
TRANSFER_PROMPT_VERSION = "transfer-v1"
TRANSFER_PROMPT_RUBRIC_VERSION = "transfer-rubric-v1"
TRANSFER_OPENED_AT_KEY = "participant_opened_at"
TRANSFER_QUALIFIED_RECALL_BOUNDARY_KEY = "qualified_recall_not_before_at"
TRANSFER_DEBRIEF_EXPOSURE_KEY = "latest_debrief_exposed_at"
TRANSFER_DEBRIEF_BOUNDARY_KEY = "latest_debrief_recall_not_before_at"


def pilot_consent_is_valid(
    consent_version: str,
    consented_at: datetime,
    *,
    now: datetime | None = None,
) -> bool:
    """Validate the frozen research disclosure and its activation instant."""

    if consent_version not in PILOT_RESEARCH_CONSENT_CATALOG:
        return False
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        return False
    if consented_at.tzinfo is None or consented_at.utcoffset() is None:
        consented_at = consented_at.replace(tzinfo=UTC)
    return consented_at.astimezone(UTC) <= (
        current.astimezone(UTC) + PILOT_CONSENT_FUTURE_SKEW
    )
