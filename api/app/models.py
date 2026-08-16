import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Postgres in production; the variant keeps the SQLite test schema compilable.
JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")

# Every timestamp column is `timestamptz` in migration 0001 and every value written
# is tz-aware (`_now()` below). A bare `datetime` annotation would map to a naive
# DateTime, and the asyncpg dialect casts bind parameters from the *model* type — so
# it would emit `$n::TIMESTAMP WITHOUT TIME ZONE` and asyncpg would reject the aware
# value with a DataError on every insert. SQLite silently drops tzinfo instead, which
# is why this only ever surfaces against Postgres.
TZ_DATETIME = DateTime(timezone=True)

DELIVERY_CONVERSATIONAL = "conversational"
DELIVERY_DESK = "desk"

# Stable migration identity for the existing private installation. The legacy
# X-API-Key may resolve only to this user; it is never accepted as a user selector.
FOUNDER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

USER_ACTIVE = "active"
USER_DELETING = "deleting"

AI_CONSENT_PENDING = "pending"
AI_CONSENT_GRANTED = "granted"
AI_CONSENT_DECLINED = "declined"
AI_CONSENT_WITHDRAWN = "withdrawn"

CARD_ACTIVE = "active"
CARD_ARCHIVED = "archived"

CAPTURE_PENDING_SOURCE = "pending_source"
CAPTURE_READY = "ready_to_review"
CAPTURE_ACTIVATED = "activated"

STATUS_OPEN = "open"
STATUS_AWAITING_FOLLOW_UP = "awaiting_follow_up"
STATUS_COMPLETE = "complete"
STATUS_ABANDONED = "abandoned"

# A card is resumable only while its session is still in one of these states.
LIVE_STATUSES = (STATUS_OPEN, STATUS_AWAITING_FOLLOW_UP)

DEFAULT_WINDOWS: list[dict[str, Any]] = [
    {"label": "Morning", "from": "07:10", "to": "08:30", "on": True},
    {"label": "Evening", "from": "21:00", "to": "22:30", "on": True},
]


def _now() -> datetime:
    return datetime.now(UTC)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    status: str = USER_ACTIVE
    is_founder: bool = False
    onboarding_completed: bool = False
    ai_consent_status: str = AI_CONSENT_PENDING
    ai_consent_version: str = ""
    ai_consent_updated_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    ai_consent_granted_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class AIConsentEvent(SQLModel, table=True):
    __tablename__ = "ai_consent_events"
    __table_args__ = (Index("ix_ai_consent_events_user_created", "user_id", "created_at"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    provider: str
    policy_version: str
    action: str
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class AppleIdentity(SQLModel, table=True):
    __tablename__ = "apple_identities"
    __table_args__ = (
        Index("uq_apple_identities_subject", "subject", unique=True),
        Index("uq_apple_identities_user", "user_id", unique=True),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    # Apple's stable, developer-team-scoped subject is the identity. Email is
    # profile data and can be a relay address, so it never participates in lookup.
    subject: str
    email: str | None = None
    display_name: str | None = None
    # Encrypted before storage by services/authentication.py. Needed to revoke
    # Sign in with Apple when the account is deleted.
    apple_refresh_token: str | None = None
    authorization_revoked_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    last_apple_event_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("uq_auth_sessions_access_hash", "access_token_hash", unique=True),
        Index("uq_auth_sessions_refresh_hash", "refresh_token_hash", unique=True),
        Index("ix_auth_sessions_user", "user_id"),
        Index("ix_auth_sessions_family", "family_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    family_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    rotated_from_id: uuid.UUID | None = None
    access_token_hash: str
    refresh_token_hash: str
    access_expires_at: datetime = Field(sa_type=TZ_DATETIME)
    refresh_expires_at: datetime = Field(sa_type=TZ_DATETIME)
    revoked_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class AuthNonce(SQLModel, table=True):
    __tablename__ = "auth_nonces"
    __table_args__ = (Index("uq_auth_nonces_hash", "nonce_hash", unique=True),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nonce_hash: str
    expires_at: datetime = Field(sa_type=TZ_DATETIME)
    used_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class Card(SQLModel, table=True):
    __tablename__ = "cards"
    __table_args__ = (
        Index("ix_cards_next_review_at", "next_review_at"),
        Index("ix_cards_user_next_review", "user_id", "next_review_at"),
        # The hot query: due conversational cards. Desk cards never enter the push loop.
        Index(
            "ix_cards_active_due_conversational",
            "next_review_at",
            postgresql_where=text(
                "delivery_mode = 'conversational' AND lifecycle_status = 'active'"
            ),
            sqlite_where=text(
                "delivery_mode = 'conversational' AND lifecycle_status = 'active'"
            ),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(default=FOUNDER_USER_ID, foreign_key="users.id", ondelete="CASCADE")
    topic: str
    category: str
    pattern: str | None = None
    source_company: str | None = None
    target_week: int | None = None
    delivery_mode: str = DELIVERY_CONVERSATIONAL

    # Generated once, then reused for every session on this card. Repeating the same
    # retrieval is the point — a fresh question each time puts every review in the
    # weak-transfer regime. Null until the first session generates one.
    canonical_question: str | None = None
    # Trusted grounding supplied by an imported guide, reviewed collection, or
    # the learner. It accompanies the relevant transcript to scoring.
    answer_anchor: str = ""
    source_excerpt: str = ""
    source_id: uuid.UUID | None = Field(
        default=None, foreign_key="material_sources.id", ondelete="SET NULL"
    )

    # Grounding is optional only for cards that predate the grounding boundary.
    # Every new activation path validates these fields before it creates a card.
    source_url: str = ""
    source_section: str = ""
    source_label: str = ""
    answer_basis: str = ""
    answer_rubric: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )

    # Archive is recoverable and leaves sessions untouched. Replacing a question
    # creates a new card and appends the pair here instead of rewriting the
    # retrieval that the old card's scores measured. Lifecycle writes serialize
    # on the oldest linked card so at most one member of the full lineage is active.
    lifecycle_status: str = CARD_ACTIVE
    archived_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    replaces_card_id: uuid.UUID | None = Field(
        default=None, foreign_key="cards.id", ondelete="SET NULL"
    )
    replaced_by_card_id: uuid.UUID | None = Field(
        default=None, foreign_key="cards.id", ondelete="SET NULL"
    )

    ease_factor: float = 2.5
    interval_days: int = 1
    repetitions: int = 0
    next_review_at: date
    last_score: int | None = None
    # The three axes behind `last_score`, denormalised from the latest complete
    # session the same way `last_score` is. Coverage's axis rollup is a mean across
    # cards, so it needs the per-card latest value, not a scan of every session.
    last_accuracy: int | None = None
    last_depth: int | None = None
    last_boundaries: int | None = None
    # Explicit semantics for `last_score` during the V1/V2 dual-read window.
    # Null only until the card has a scored result.
    last_score_contract_version: int | None = None
    last_reviewed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    mastery_summary: str = ""
    # Compliance signal only — never feeds SM-2.
    missed_count: int = 0
    last_pushed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    # Opening the explicit Learn surface exposes trusted answer authority. The
    # exposure is recorded separately from review history and temporarily gates
    # every scored session, including Practice, so a same-session answer cannot
    # masquerade as unaided recall. Neither field is part of SM-2.
    last_learning_exposure_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    recall_not_before_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    # The `last_pushed_at` value check-missed has already counted — a push instant,
    # not a counting instant. Equal means counted; NULL or older means this push is
    # still uncounted. Replaces clearing `last_pushed_at`, which destroyed the
    # evidence both the daily cap and the per-window guard read.
    missed_counted_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class PendingCapture(SQLModel, table=True):
    """A fast-captured gap that has not entered the review system.

    This is a separate table on purpose: no due, push, scoring, Sprint, Coverage,
    history, or scheduler query can accidentally include a capture as a card.
    Draft rubric and question fields make the multi-screen grounding flow durable
    and make question generation idempotent across retries.
    """

    __tablename__ = "pending_captures"
    __table_args__ = (
        Index("ix_pending_captures_user_created", "user_id", text("created_at DESC")),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        default=FOUNDER_USER_ID, foreign_key="users.id", ondelete="CASCADE"
    )
    topic: str
    context: str = ""
    status: str = CAPTURE_PENDING_SOURCE

    source_url: str = ""
    source_section: str = ""
    source_label: str = ""
    answer_basis: str = ""
    answer_rubric: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    canonical_question: str = ""

    # Kept after activation so a lost response can replay safely and return the
    # same card rather than creating a duplicate.
    activated_card_id: uuid.UUID | None = Field(
        default=None, foreign_key="cards.id", ondelete="SET NULL"
    )

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class Session(SQLModel, table=True):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_card_started", "card_id", text("started_at DESC")),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    card_id: uuid.UUID = Field(foreign_key="cards.id", ondelete="CASCADE")

    question_asked: str
    # Frozen legacy, both of them: the single probe a session could once carry.
    # Migration 0015 moved probes to `session_probes` and left these holding the
    # history they already had. Nothing reads or writes them — see DEVIATIONS §30.
    follow_up_question: str | None = None
    answer_text: str = ""
    follow_up_answer: str = ""
    # In-progress, unsubmitted. Losing this is the worst failure mode in the product.
    draft_text: str = ""

    # Composite, derived in code from the three axes below — never model-produced.
    # Null on rows written before the decomposition shipped; those keep their
    # original blended score for history display.
    score: int | None = None
    accuracy: int | None = None
    depth: int | None = None
    boundaries: int | None = None
    feedback: str = ""
    # Still written, still truthful: "a scored probe was issued in this session".
    # How many, and which, is `session_probes`.
    follow_up_used: bool = False
    # V1 remains active until the staged V2 release gate is complete. Every row
    # carries its meaning so historical composites are never relabeled as Recall.
    scoring_contract_version: int = 1
    # Server-selected provider/model/effort binding for every scored turn in this
    # session. Empty means a historical session and resolves to Anthropic. JSON
    # keeps the binding additive as the separately qualified route evolves.
    scoring_route: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )

    # The V2 score transaction freezes focus/question for the optional qualitative
    # turn; that later endpoint writes answer/feedback against the stored prompt.
    coaching_focus: str | None = None
    coaching_question: str | None = None
    coaching_answer: str | None = None
    coaching_feedback: str | None = None

    # Turn 3: a coached re-attempt, offered only after the correction has been
    # stated and only when the mechanism was wrong. Scored on one axis, written to
    # the mastery summary, and barred from SM-2 and from `score` — it measures
    # coached performance, not recall. See docs/multi-turn-coaching-design.md §4.
    reattempt_answer: str = ""
    reattempt_accuracy: int | None = None
    # Guards replay, and named to mirror `follow_up_used` — the other structural cap
    # in this table. "Used", not "offered": the session completes either way, so
    # nothing here distinguishes an offer declined from one never made.
    reattempt_used: bool = False
    # A Review Sprint run. Scored and written to history exactly like a normal
    # session; the card's SM-2 fields are left untouched.
    practice: bool = False
    status: str = STATUS_OPEN

    started_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    ended_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)


class SessionProbe(SQLModel, table=True):
    """One scored follow-up probe, in the order it was asked.

    Rows here are scored and pre-correction by definition, which is what keeps the
    coached re-attempt and the coaching turn on `Session` as scalars — those happen
    after the correction has been stated and never reach SM-2. See migration 0015
    and docs/multi-turn-coaching-design.md §5.1.
    """

    __tablename__ = "session_probes"
    __table_args__ = (Index("uq_session_probes_session_idx", "session_id", "idx", unique=True),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(foreign_key="sessions.id", ondelete="CASCADE")
    # 1-based. The ceiling is `llm.MAX_SCORED_FOLLOW_UPS`, enforced in code so the
    # cap stays one decision; the schema only knows that order starts at 1.
    idx: int
    question: str
    # "" until this probe is answered — a row is written when the question is issued.
    answer: str = ""
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class DeviceToken(SQLModel, table=True):
    __tablename__ = "device_tokens"
    __table_args__ = (Index("ix_device_tokens_user", "user_id"),)

    token: str = Field(primary_key=True)
    user_id: uuid.UUID = Field(default=FOUNDER_USER_ID, foreign_key="users.id", ondelete="CASCADE")
    kind: str = "apns"
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class Settings(SQLModel, table=True):
    __tablename__ = "settings"
    __table_args__ = (Index("uq_settings_user", "user_id", unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: uuid.UUID = Field(default=FOUNDER_USER_ID, foreign_key="users.id", ondelete="CASCADE")
    reviews_per_day: int = 2
    windows: list[dict[str, Any]] = Field(
        default_factory=lambda: list(DEFAULT_WINDOWS),
        sa_column=Column(JSON_TYPE, nullable=False),
    )
    timezone: str = "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Public study material — proposals first, cards only after confirmation.
# ---------------------------------------------------------------------------

SOURCE_DRAFT = "draft"
SOURCE_PENDING = "pending"
SOURCE_PROCESSING = "processing"
SOURCE_READY = "ready"
SOURCE_NEEDS_ATTENTION = "needs_attention"
SOURCE_FAILED = "failed"
SOURCE_CONFIRMED = "confirmed"
SOURCE_SUPERSEDED = "superseded"

PROPOSAL_CLEAN = "clean"
PROPOSAL_NEEDS_ATTENTION = "needs_attention"
PROPOSAL_EXCLUDED = "excluded"
PROPOSAL_CONFIRMED = "confirmed"

CONTENT_PROVENANCE_LEGACY_UNSPECIFIED = "legacy_unspecified"
CONTENT_PROVENANCE_EXACT_SOURCE_EXCERPT = "exact_source_excerpt"
CONTENT_PROVENANCE_LEARNER_NOTES = "learner_notes"
CONTENT_PROVENANCE_COACHED_CORRECTION = "coached_correction"
CONTENT_PROVENANCE_AI_DERIVED_SUMMARY = "ai_derived_summary"


class MaterialSource(SQLModel, table=True):
    __tablename__ = "material_sources"
    __table_args__ = (
        Index("ix_material_sources_user_status", "user_id", "status"),
        Index(
            "uq_material_sources_version",
            "user_id",
            "lineage_id",
            "version",
            unique=True,
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    lineage_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    previous_version_id: uuid.UUID | None = Field(
        default=None, foreign_key="material_sources.id", ondelete="SET NULL"
    )
    version: int = 1
    kind: str = "guide"
    title: str
    # Verbatim. File text is extracted on-device before upload.
    source_text: str
    # Provenance only. The import worker never fetches this URL; source_text is
    # still the exact authority transmitted for extraction and scoring.
    source_url: str = ""
    # What the pasted content is, independently from its genre and attribution.
    # Existing rows remain explicitly unclassified until the learner chooses.
    content_provenance: str = CONTENT_PROVENANCE_LEGACY_UNSPECIFIED
    original_filename: str = ""
    mime_type: str = "text/plain"
    import_path: str = "topics"
    intent: str = "already_studied"
    status: str = SOURCE_DRAFT
    # A worker must atomically replace both fields before it may transmit the
    # guide or store a result.  The heartbeat lets a later process recover an
    # orphaned claim without duplicating a still-live multi-minute import.
    processing_run_id: uuid.UUID | None = None
    processing_heartbeat_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    requested_weeks: int = 12
    weekly_capacity_minutes: int = 480
    mode: str = "flexible"
    deadline: date | None = None
    plan_draft_id: uuid.UUID | None = Field(
        default=None, foreign_key="study_plan_guide_drafts.id", ondelete="SET NULL"
    )
    result_summary: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    # Distilled artifacts are deliberately separate from the verbatim source.
    # They are written only from confirmed concept proposals after the lesson's
    # first-pass recall is complete, never from session transcripts.
    canonical_note_markdown: str = ""
    recall_export_markdown: str = ""
    distilled_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    error: str = ""
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class MaterialTopicProposal(SQLModel, table=True):
    __tablename__ = "material_topic_proposals"
    __table_args__ = (
        Index("ix_material_topic_proposals_source", "source_id", "position"),
        Index("uq_material_topic_proposals_card_id", "card_id", unique=True),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_id: uuid.UUID = Field(foreign_key="material_sources.id", ondelete="CASCADE")
    position: int
    section_title: str = ""
    topic: str
    answer_anchor: str
    source_excerpt: str = ""
    # Lesson extraction crosses the same complete-grounding boundary as Capture
    # and Study Plan proposals. Legacy guide/manual proposals keep empty defaults.
    canonical_question: str = ""
    answer_rubric: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    # Five durable practice/export cues for one concept. The scheduled Card still
    # owns exactly one canonical question so mastery and SM-2 are not fragmented.
    recall_questions: list[dict[str, str]] = Field(
        default_factory=list, sa_column=Column(JSON_TYPE, nullable=False)
    )
    status: str = PROPOSAL_CLEAN
    issue: str = ""
    merged_into_id: uuid.UUID | None = Field(
        default=None, foreign_key="material_topic_proposals.id", ondelete="SET NULL"
    )
    # Stable concept -> mastery join. Set in the same confirmation transaction
    # that creates the Card; null for unconfirmed and legacy pre-0018 proposals.
    card_id: uuid.UUID | None = Field(
        default=None, foreign_key="cards.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class LLMUsage(SQLModel, table=True):
    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_user_created", "user_id", "created_at"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    operation: str
    # Privacy-safe operational evidence only: provider/model/route, outcome,
    # latency and token counts. Never prompt, transcript, grounding, or output.
    details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


# ---------------------------------------------------------------------------
# Study Plan — see docs/STUDY-PLAN-SPEC.md
#
# Nothing below writes to `cards` or `sessions`. The only link between the two
# halves of the schema is `study_plan_card_links`, and it is only ever written
# by a committed card-proposal acceptance.
# ---------------------------------------------------------------------------

PLAN_ACTIVE = "active"
PLAN_PAUSED = "paused"
PLAN_COMPLETED = "completed"
# Completed is not archived. Both are terminal for scheduling; only one means
# the work was finished, and the Plans sheet lists them separately.
PLAN_ARCHIVED = "archived"

MODE_FLEXIBLE = "flexible"
MODE_FIXED = "fixed"

ITEM_LEARN = "learn"
ITEM_PRACTICE = "practice"
ITEM_RETRIEVE = "retrieve"

PRIORITY_CORE = "core"
PRIORITY_OPTIONAL = "optional"
PRIORITY_RECURRING = "recurring"

ITEM_PENDING = "pending"
ITEM_COMPLETE = "complete"
ITEM_DEFERRED = "deferred"
# `removed` is out of the plan entirely; `deferred` is out of the schedule but
# kept for the recap.
ITEM_REMOVED = "removed"

ORIGIN_IMPORTED = "imported"
ORIGIN_GENERATED = "generated"
ORIGIN_MANUAL = "manual"

ESTIMATE_IMPORTED = "imported"
ESTIMATE_GENERATED = "generated"
ESTIMATE_USER_EDITED = "user_edited"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_NEEDS_REVIEW = "needs_review"

DEP_HARD = "hard"
DEP_SOFT = "soft"

DEP_IMPORTED = "imported"
DEP_INFERRED = "inferred"
DEP_USER_ADDED = "user_added"

# Revision kinds. Every material schedule change writes one; display-only edits
# (an overview title, a note) do not.
REVISION_CREATED = "created"
REVISION_CAPACITY = "capacity"
REVISION_REPLAN = "replan"
REVISION_REOPEN = "reopen"
REVISION_RESUME = "resume"
REVISION_PAUSE = "pause"
REVISION_ACTIVATE = "activate"
REVISION_COMPLETE = "complete"
REVISION_ARCHIVE = "archive"
REVISION_ITEM_EDIT = "item_edit"

DRAFT_PENDING = "pending"
DRAFT_READY = "ready"
DRAFT_FAILED = "failed"

# A candidate is selectable only when all five gate questions passed. Everything
# else is displayed under NOT SUGGESTED with no action, or resolved by the user.
DISPOSITION_SUGGESTED = "suggested"
DISPOSITION_NOT_SUGGESTED = "not_suggested"
DISPOSITION_EXISTING = "existing"
DISPOSITION_POSSIBLE_OVERLAP = "possible_overlap"
DISPOSITION_ACCEPTED = "accepted"
DISPOSITION_SKIPPED = "skipped"

DUPLICATE_NONE = "none"
DUPLICATE_EXACT = "exact"
DUPLICATE_POSSIBLE = "possible"

ACCEPTANCE_PROCESSING = "processing"
ACCEPTANCE_COMMITTED = "committed"
ACCEPTANCE_FAILED = "failed"


class StudyPlan(SQLModel, table=True):
    __tablename__ = "study_plans"
    __table_args__ = (
        # At most one active plan, enforced by the database rather than by a
        # read-then-write in Python. Zero active is valid, which is why this is a
        # partial index and not a one-row table. Both engines honour partial
        # unique indexes, so the SQLite suite exercises the production rule.
        Index(
            "uq_study_plans_one_active",
            "user_id",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_study_plans_status", "user_id", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(default=FOUNDER_USER_ID, foreign_key="users.id", ondelete="CASCADE")
    title: str
    subject: str
    # Normalised subject key. Card proposals require this to be in the technical
    # allowlist AND the importer to have said the subject is supported — either
    # alone is not enough. See services/study_plan.subject_supports_cards.
    subject_slug: str
    # Stored verbatim. Source offsets on items index into this exact string, so
    # it must never be reformatted, trimmed, or normalised after import.
    guide_text: str

    status: str = PLAN_ACTIVE
    mode: str = MODE_FLEXIBLE
    # The latest acceptable completion date on a fixed plan. Finishing earlier is
    # valid; the deadline is a ceiling, not a target.
    deadline: date | None = None
    start_date: date

    default_weekly_capacity_minutes: int
    # 1-based. The unambiguous progress anchor: plan week N runs from
    # start_date + 7*(N-1) for seven days, so every "week of <date>" label and
    # every forecast is derived from these two fields and nothing else.
    current_week_index: int = 1
    forecast_end_plan_week: int

    # Optimistic concurrency. Bumped by every material schedule write; every
    # proposal carries the revision it was computed against, and applying a stale
    # one is a 409 rather than a silent overwrite.
    revision: int = 1

    paused_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    completed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    archived_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanPhase(SQLModel, table=True):
    __tablename__ = "study_plan_phases"
    __table_args__ = (Index("ix_study_plan_phases_plan", "plan_id", "index"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    index: int

    full_title: str
    # 2-5 words, generated during Preview, user-editable. Display only: it never
    # reaches scheduling, dependency, duplicate, card, or scoring logic, and the
    # accessible name always uses full_title.
    overview_title: str = ""
    # Surfaced at plan creation and in supporting detail — deliberately not on the
    # overview, which V3.5 stripped of curriculum paragraphs.
    description: str = ""


class StudyPlanWeek(SQLModel, table=True):
    __tablename__ = "study_plan_weeks"
    __table_args__ = (Index("ix_study_plan_weeks_plan", "plan_id", "index"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    phase_id: uuid.UUID = Field(foreign_key="study_plan_phases.id", ondelete="CASCADE")
    index: int

    full_title: str
    overview_title: str = ""
    # One-week override. Null means the plan default applies — storing the default
    # here instead would silently pin the week when the plan default later moves.
    override_capacity_minutes: int | None = None
    advanced_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanItem(SQLModel, table=True):
    __tablename__ = "study_plan_items"
    __table_args__ = (
        Index("ix_study_plan_items_week", "week_id", "guide_order"),
        Index("ix_study_plan_items_plan", "plan_id"),
        Index(
            "uq_study_plan_items_curriculum_key",
            "plan_id",
            "curriculum_key",
            unique=True,
            postgresql_where=text("curriculum_key IS NOT NULL"),
            sqlite_where=text("curriculum_key IS NOT NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    phase_id: uuid.UUID = Field(foreign_key="study_plan_phases.id", ondelete="CASCADE")
    # A real foreign key rather than the handoff's bare week_index, so a replan
    # moves one column and cannot leave an item pointing at a week that no longer
    # exists. Ordering still comes from StudyPlanWeek.index.
    week_id: uuid.UUID = Field(foreign_key="study_plan_weeks.id", ondelete="CASCADE")
    # Original order within the phase, from the guide. Rule 4 of the scheduler
    # preserves it, so it survives every replan.
    guide_order: int

    # Stable only within a plan. First-party manifests use this to update the
    # same item in place across curriculum versions without relying on an
    # editable title. Generic imports leave it null.
    curriculum_key: str | None = None

    type: str
    priority: str
    full_title: str
    why_it_matters: str = ""
    done_when: str = ""

    # Integer minutes, 30-minute increments. Hours are display only.
    estimate_minutes: int
    estimate_source: str = ESTIMATE_IMPORTED
    estimate_confidence: str = CONFIDENCE_HIGH

    status: str = ITEM_PENDING
    origin: str = ORIGIN_IMPORTED
    # Set on a retrieval activity generated from a Learn or Practice item; the
    # scheduler places it after its source (rule 6).
    source_item_id: uuid.UUID | None = Field(
        default=None, foreign_key="study_plan_items.id", ondelete="SET NULL"
    )
    # Character offsets into StudyPlan.guide_text. Validated against the verbatim
    # text at import; a mismatch is a blocking preview check, not a warning.
    source_start: int | None = None
    source_end: int | None = None
    source_excerpt: str = ""
    # Explicit per-item decision. Subject eligibility is necessary but not
    # sufficient: a broad mock can be valuable plan work without containing a
    # trustworthy, bounded answer basis for a recall card.
    recall_supported: bool = False
    # What the importer understood this line of the guide to mean. Shown in the
    # estimate and retrieval audits so a wrong reading is correctable.
    parser_interpretation: str = ""
    # Actionable external study material. These are navigation/provenance only:
    # a URL to paid material is never treated as an answer basis.
    resources: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON_TYPE, nullable=False)
    )
    # Exact card topics associated with this activity. Resolution is read-only;
    # Study Plan never creates a link to or mutates an existing card.
    mapped_recall_topics: list[str] = Field(
        default_factory=list, sa_column=Column(JSON_TYPE, nullable=False)
    )
    # Optional extra work shown outside the scheduled-capacity calculation.
    # It has no completion state and cannot create carry-forward debt.
    stretch_actions: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON_TYPE, nullable=False)
    )
    # Generated retrieval only: when the user approved it during Preview. A
    # generated retrieval item with no approval never reaches a created plan.
    approved_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    completed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    reopened_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    notes: str = ""
    # Optional local reminder. Deliberately outside every schedule calculation:
    # it is not capacity, not a dependency, and not a forecast input. Missing one
    # changes nothing.
    study_block_label: str = ""
    study_block_weekday: int | None = None
    study_block_minute_of_day: int | None = None
    study_block_reminder_on: bool = False

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanItemDependency(SQLModel, table=True):
    __tablename__ = "study_plan_item_dependencies"
    __table_args__ = (Index("ix_study_plan_deps_plan", "plan_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    prerequisite_item_id: uuid.UUID = Field(foreign_key="study_plan_items.id", ondelete="CASCADE")
    dependent_item_id: uuid.UUID = Field(foreign_key="study_plan_items.id", ondelete="CASCADE")

    kind: str = DEP_HARD
    source: str = DEP_IMPORTED
    confidence: str = CONFIDENCE_HIGH
    rationale: str = ""
    source_excerpt: str = ""
    # Strong imported ordering is accepted automatically and never reaches the
    # audit, so most rows are confirmed at import. Null means the conservative
    # ordering is in force and the audit explains why.
    confirmed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanRevision(SQLModel, table=True):
    __tablename__ = "study_plan_revisions"
    __table_args__ = (Index("ix_study_plan_revisions_plan", "plan_id", text("created_at DESC")),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    kind: str
    # The plan revision this change was computed against. Together with
    # StudyPlan.revision it is what makes a stale proposal a 409.
    base_plan_revision: int
    before: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    after: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False))
    # Only set where an inverse is genuinely derivable from `before`. A capacity
    # change is reversible; a completion that triggered a carry-forward is not.
    reversible: bool = False
    summary: str = ""

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanGuideDraft(SQLModel, table=True):
    """A pasted guide and its import attempt, before any plan exists.

    Persisted so a failed preview can be retried server-side without re-uploading
    the guide, and so the user's duration, capacity, mode, deadline, edits, and
    review choices survive a client crash. Creating a plan from a draft does not
    delete it — the provenance is what `plan.guide_text` is checked against.
    """

    __tablename__ = "study_plan_guide_drafts"
    __table_args__ = (Index("ix_study_plan_guide_drafts_user", "user_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(default=FOUNDER_USER_ID, foreign_key="users.id", ondelete="CASCADE")
    guide_text: str
    requested_weeks: int
    weekly_capacity_minutes: int
    mode: str = MODE_FLEXIBLE
    deadline: date | None = None
    start_date: date
    subject_hint: str = ""
    title_hint: str = ""

    status: str = DRAFT_PENDING
    # The importer's validated output plus every user edit applied on top. This is
    # what POST /study-plans reads; the model is never consulted again.
    preview: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    # The raw model response, kept for the retry path and for debugging a
    # validation failure without a second paid call.
    raw_response: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    checks: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON_TYPE, nullable=False)
    )
    error: str = ""

    # A preview can spend eleven minutes at the provider.  The token and
    # heartbeat make that paid work a lease: one request owns the result, a
    # concurrent Retry cannot transmit the guide again, and a late response
    # cannot overwrite a newer claimant.
    processing_run_id: uuid.UUID | None = None
    processing_heartbeat_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanPracticeDebrief(SQLModel, table=True):
    """One durable, unscored reflection for a completed Practice item.

    The user's words may identify a gap, but never become the answer authority for
    a card. Card generation still receives the item's trusted source excerpt and
    the existing five-question gate still decides what may be accepted.
    """

    __tablename__ = "study_plan_practice_debriefs"
    __table_args__ = (
        Index(
            "uq_study_plan_practice_debriefs_item",
            "plan_item_id",
            unique=True,
        ),
        Index("ix_study_plan_practice_debriefs_plan", "plan_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    plan_item_id: uuid.UUID = Field(foreign_key="study_plan_items.id", ondelete="CASCADE")
    # Cheap, idempotent server backup for the disk-first iOS draft.
    draft_text: str = ""
    # Immutable after submission in v1. Reopening the item does not clear it.
    text: str = ""
    submitted_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanCardProposal(SQLModel, table=True):
    """A candidate recall card, generated after its source item was completed.

    Existence is not permission. A proposal is selectable only when all five gate
    questions passed and the duplicate check is clean; nothing here creates a card.
    """

    __tablename__ = "study_plan_card_proposals"
    __table_args__ = (Index("ix_study_plan_card_proposals_item", "source_plan_item_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    source_plan_item_id: uuid.UUID = Field(foreign_key="study_plan_items.id", ondelete="CASCADE")
    # Bumped by an edit. A new revision invalidates any acceptance intent built
    # against the old one, which is what stops an edited card being added twice.
    revision: int = 1

    topic: str
    category: str = "Unsorted"
    # Persisted onto the card as `canonical_question` when accepted, so an
    # approved question is never regenerated on the first review.
    canonical_question: str
    # Snapshot the trusted item authority and the model's source-grounded rubric
    # on the proposal. Acceptance is then deterministic and makes no second model
    # call, even if the plan item is edited while the user reviews candidates.
    source_label: str = ""
    answer_basis: str = ""
    answer_rubric: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSON_TYPE, nullable=False)
    )
    reason: str = ""

    # Five {question, passed, reason} objects, in gate order. All five must pass.
    gate_results: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON_TYPE, nullable=False)
    )
    duplicate_check_result: str = DUPLICATE_NONE
    duplicate_card_id: uuid.UUID | None = Field(
        default=None, foreign_key="cards.id", ondelete="SET NULL"
    )
    disposition: str = DISPOSITION_SUGGESTED
    # normalize_topic(topic) at proposal time. Recomputed and rechecked inside the
    # acceptance transaction — this column is a cache, never the authority.
    normalized_topic: str

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanCardProposalAcceptance(SQLModel, table=True):
    """One atomic, idempotent attempt to turn selected proposals into cards."""

    __tablename__ = "study_plan_card_proposal_acceptances"
    __table_args__ = (Index("uq_study_plan_acceptance_key", "idempotency_key", unique=True),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    proposal_id: uuid.UUID = Field(foreign_key="study_plan_card_proposals.id", ondelete="CASCADE")
    idempotency_key: str
    # Hash of selected ids + edits. Same key with a different hash is a conflict,
    # not a replay — that combination means the client changed its mind mid-retry.
    request_hash: str
    proposal_revision: int
    status: str = ACCEPTANCE_PROCESSING
    created_card_ids: list[str] = Field(
        default_factory=list, sa_column=Column(JSON_TYPE, nullable=False)
    )
    committed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanCardLink(SQLModel, table=True):
    """The only edge between Study Plan and the review schedule.

    Linkage lives here rather than as a column on `cards` so "Study Plan never
    modifies an existing card" is structural. Deleting a card removes the link;
    deleting a plan removes the link and leaves the card, its score, its sessions,
    and its SM-2 state exactly as they were.
    """

    __tablename__ = "study_plan_card_links"
    __table_args__ = (Index("ix_study_plan_card_links_plan", "plan_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    plan_item_id: uuid.UUID = Field(foreign_key="study_plan_items.id", ondelete="CASCADE")
    card_id: uuid.UUID = Field(foreign_key="cards.id", ondelete="CASCADE")
    acceptance_id: uuid.UUID = Field(
        foreign_key="study_plan_card_proposal_acceptances.id", ondelete="CASCADE"
    )

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class StudyPlanDuplication(SQLModel, table=True):
    __tablename__ = "study_plan_duplications"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")
    duplicated_plan_id: uuid.UUID = Field(foreign_key="study_plans.id", ondelete="CASCADE")

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
