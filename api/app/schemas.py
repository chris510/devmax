import uuid
from datetime import date, datetime, time
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.services.scoring_contract import CoachingFocus, ScoreKind, ScoringContractVersion


class DueCard(BaseModel):
    id: uuid.UUID
    topic: str
    category: str
    mastery_summary: str
    last_score: int | None
    recall_score: int | None
    score_kind: ScoreKind
    scoring_contract_version: ScoringContractVersion | None
    due_label: str
    resumable: bool
    missed_count: int


class CardSummary(BaseModel):
    id: uuid.UUID
    topic: str
    category: str
    delivery_mode: str
    mastery_summary: str
    last_score: int | None
    recall_score: int | None
    score_kind: ScoreKind
    scoring_contract_version: ScoringContractVersion | None
    # The three axes behind `last_score`. Coverage's rollup means these across the
    # library; nothing else consumes them.
    last_accuracy: int | None = None
    last_depth: int | None = None
    last_boundaries: int | None = None
    ease_factor: float
    interval_days: int
    repetitions: int
    next_review_at: date
    # An answer-authority exposure can hold recall beyond the SM-2 date without
    # rewriting it. Null means no such hold exists.
    recall_not_before_at: datetime | None = None
    # Both computed server-side so the client never reimplements date math.
    due_label: str
    days_since_review: int | None
    missed_count: int
    lifecycle_status: Literal["active", "archived"] = "active"


class Turn(BaseModel):
    role: Literal["question", "answer", "follow_up", "score"]
    text: str


class SessionHistory(BaseModel):
    id: uuid.UUID
    date: datetime
    score: int | None
    recall_score: int | None
    legacy_composite_score: int | None
    scoring_contract_version: ScoringContractVersion
    feedback: str
    turns: list[Turn]
    coaching_focus: Literal["depth", "boundaries"] | None = None
    coaching_question: str | None = None
    coaching_answer: str | None = None
    coaching_feedback: str | None = None


class CardDetail(CardSummary):
    learning_available: bool = False
    source_label: str = ""
    source_section: str = ""
    sessions: list[SessionHistory]


class CardLearningOut(BaseModel):
    """Trusted material returned only after its recall hold is committed.

    The canonical question is deliberately absent. Learning should establish the
    mechanism, not teach the exact cue that every future review will reuse.
    """

    card_id: uuid.UUID
    topic: str
    category: str
    source_url: str
    source_section: str
    source_label: str
    source_excerpt: str
    core_explanation: str
    essential_account: str
    acceptable_alternative: str
    depth_extension: str
    boundary_extension: str
    misconception: str
    recall_available_at: datetime


class TierCard(BaseModel):
    id: uuid.UUID
    topic: str
    mastery_summary: str
    last_score: int | None = None
    recall_score: int | None = None
    score_kind: ScoreKind = "unrated"
    scoring_contract_version: ScoringContractVersion | None = None
    days_overdue: int | None = None


class Overview(BaseModel):
    counts: dict[str, int]
    shaky: list[TierCard]
    cold: list[TierCard]


class AnswerRubric(BaseModel):
    mechanism: str = Field(
        min_length=1,
        max_length=4000,
        validation_alias=AliasChoices("mechanism", "essential_account"),
    )
    acceptable_alternative: str = Field(min_length=1, max_length=4000)
    trade_off: str = Field(
        min_length=1,
        max_length=4000,
        validation_alias=AliasChoices("trade_off", "depth_extension"),
    )
    failure_mode: str = Field(
        min_length=1,
        max_length=4000,
        validation_alias=AliasChoices("failure_mode", "boundary_extension"),
    )
    misconception: str = Field(min_length=1, max_length=4000)


class CaptureCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    context: str = Field(default="", max_length=1000)


class GroundingUpdate(BaseModel):
    source_url: str | None = Field(default=None, max_length=4000)
    source_section: str | None = Field(default=None, max_length=1000)
    source_label: str | None = Field(default=None, max_length=1000)
    answer_basis: str | None = Field(default=None, max_length=20_000)
    answer_rubric: AnswerRubric | None = None
    canonical_question: str | None = Field(default=None, max_length=4000)


class CaptureUpdate(GroundingUpdate):
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    context: str | None = Field(default=None, max_length=1000)


class CaptureSummary(BaseModel):
    id: uuid.UUID
    topic: str
    context: str
    status: Literal["pending_source", "ready_to_review", "activated"]
    needs_source: bool
    created_at: datetime
    updated_at: datetime


class CaptureOut(BaseModel):
    id: uuid.UUID
    topic: str
    context: str
    status: Literal["pending_source", "ready_to_review", "activated"]
    source_url: str
    source_section: str
    source_label: str
    answer_basis: str
    answer_rubric: dict[str, str]
    canonical_question: str
    activated_card_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class CaptureActivate(BaseModel):
    schedule: Literal["now", "next"]


class ReplaceCard(BaseModel):
    canonical_question: str = Field(min_length=1, max_length=4000)
    schedule: Literal["now", "next"]


class CardGroundingUpdate(GroundingUpdate):
    canonical_question: str | None = Field(default=None, min_length=1, max_length=4000)


class CardMaintenance(BaseModel):
    id: uuid.UUID
    lifecycle_status: Literal["active", "archived"]
    canonical_question: str
    source_url: str
    source_section: str
    source_label: str
    answer_basis: str
    answer_rubric: dict[str, str]
    replaces_card_id: uuid.UUID | None
    replaced_by_card_id: uuid.UUID | None


class SessionStart(BaseModel):
    session_id: uuid.UUID
    question: str
    is_follow_up: bool
    # 0 for the opening answer; a pending probe's 1-based `idx` thereafter.
    # The client echoes this with the answer so repeated text on adjacent turns
    # cannot be mistaken for a retry.
    turn_index: int = Field(ge=0)
    draft_text: str
    resumed: bool


class DraftUpdate(BaseModel):
    draft_text: str = ""
    # Same turn coordinate as `ScoredAnswerIn`. A stale upload is acknowledged
    # but ignored after the session advances, so it cannot resurrect turn N's
    # transcript under turn N+1.
    turn_index: int | None = Field(default=None, ge=0)


class AnswerIn(BaseModel):
    text: str = ""


class ScoredAnswerIn(AnswerIn):
    # Optional only for compatibility with clients shipped before turn-aware
    # idempotency. A terminal response can be replayed only when this is present.
    turn_index: int | None = Field(default=None, ge=0)


class FollowUpOut(BaseModel):
    status: Literal["follow_up"] = "follow_up"
    question: str
    turn_index: int = Field(ge=1)


class CompleteOut(BaseModel):
    status: Literal["complete"] = "complete"
    score: int
    recall_score: int
    scoring_contract_version: ScoringContractVersion
    feedback: str
    next_review_at: date
    interval_days: int
    # A practice run left the schedule alone; the two fields above are the card's
    # unchanged values, and the session-end line says so instead of quoting them.
    practice: bool = False
    # Whether the client should offer the coached re-attempt. Server-computed so the
    # `accuracy <= 2` gate lives in one place and the app never sees a
    # per-axis score it has nowhere to display.
    reattempt_offered: bool = False
    # The exact prompt to show for turn 3, or null when it isn't offered. Sent by the
    # server for the same reason turns 1 and 2 are: it is the question the answer
    # will be graded against, and the client cannot reliably reconstruct it — on a
    # resumed follow-up session the client's copy of "the question" is the probe.
    reattempt_prompt: str | None = None
    coaching_offered: bool = False
    coaching_focus: CoachingFocus | None = None
    coaching_question: str | None = None


class ReattemptOut(BaseModel):
    """Deliberately carries no score.

    Turn 3's `accuracy` is stored but never returned — the app shows one
    numeral per session and that numeral is turn 2's composite. Sending a second
    number to a client that has no place to put it invites putting it somewhere.
    """

    mastery_summary: str


class CoachingOut(BaseModel):
    focus: CoachingFocus
    question: str
    feedback: str


class DeviceTokenIn(BaseModel):
    token: str = Field(min_length=1)
    kind: str = "apns"


class AuthNonceOut(BaseModel):
    nonce: str


class AppleSignInIn(BaseModel):
    identity_token: str = Field(min_length=1)
    authorization_code: str = Field(min_length=1)
    nonce: str = Field(min_length=16)
    display_name: str | None = Field(default=None, max_length=200)


class RefreshTokenIn(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime
    refresh_expires_at: datetime


class CurrentUserOut(BaseModel):
    id: uuid.UUID
    onboarding_completed: bool
    is_founder: bool
    display_name: str = ""
    email: str = ""
    apple_user_identifier: str = ""
    ai_consent_status: str
    ai_consent_version: str
    ai_consent_updated_at: datetime | None
    ai_processing_allowed: bool
    ai_consent_prompt_required: bool


class AIConsentIn(BaseModel):
    action: Literal["grant", "decline", "withdraw"]
    # The client sends the version embedded beside the disclosure it rendered.
    # Optional at the schema boundary so even an older client can always withdraw;
    # the service recognizes the omitted version as the original Anthropic-only
    # client and accepts it only while that policy still satisfies deployment.
    policy_version: str | None = Field(default=None, max_length=128)


class AIConsentOut(BaseModel):
    provider: str
    policy_version: str
    status: Literal["granted", "declined", "withdrawn"]
    updated_at: datetime
    processing_allowed: bool
    prompt_required: bool


class AppleServerNotificationIn(BaseModel):
    payload: str = Field(min_length=1, max_length=16_384)


class NotificationWindow(BaseModel):
    label: str
    on: bool
    # `from` is a Python keyword, so the field is aliased to match the wire format.
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class SettingsBase(BaseModel):
    reviews_per_day: int = Field(ge=1, le=6)
    timezone: str


class SettingsOut(SettingsBase):
    windows: list[NotificationWindow]
    active_scoring_contract_version: ScoringContractVersion


# A window shorter than the cron's poll interval can fall between two polls and
# never fire, so this is the floor. Pinned against the actual schedule in
# .github/workflows/trigger-review.yml by
# `test_the_minimum_window_is_at_least_the_poll_interval` — a comment alone is
# the same hand-synced coupling this whole change set exists to remove.
MIN_WINDOW_MINUTES = 30


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


class NotificationWindowIn(NotificationWindow):
    """A notification window on the write path, where the rules apply.

    Deliberately a separate model from `NotificationWindow`: `read_settings`
    rebuilds that one from stored JSON, so a rule there would make
    `GET /settings` fail on any row written before the rule existed — including
    the hand-widened windows docs/RUNBOOK.md describes for testing a push. Reads
    stay permissive; writes are constrained.

    Validating per window rather than across the whole settings object also puts
    the offending index in the 422's `loc`, so a client can point at the window
    it rejected instead of the request body as a whole.
    """

    @model_validator(mode="after")
    def _usable(self) -> "NotificationWindowIn":
        try:
            span = _minutes(time.fromisoformat(self.to)) - _minutes(time.fromisoformat(self.from_))
        except ValueError as exc:
            raise ValueError("times must be 24-hour HH:MM") from exc
        if span < MIN_WINDOW_MINUTES:
            # A non-positive span covers `to` before `from`, and `to == from` —
            # both two taps away in the app, which advances each end separately.
            raise ValueError(
                f"a window must run at least {MIN_WINDOW_MINUTES} minutes; this one is {span}"
            )
        return self


class SettingsIn(SettingsBase):
    windows: list[NotificationWindowIn]


class TriggerResult(BaseModel):
    sent: bool
    # Constrained because this is what the cron consumer branches on — a typo'd
    # reason should fail here, not read as an unrecognised-but-valid state.
    reason: (
        Literal[
            "outside_window",
            # This window already produced a push...
            "already_pushed",
            # ...versus: every due card was already offered earlier today. Two
            # different facts, kept apart so a log tells you which one happened.
            "already_offered",
            "daily_limit",
            "nothing_due",
            "no_devices",
        ]
        | None
    ) = None
    card_id: uuid.UUID | None = None
    due_count: int | None = None


class TriggerBatchResult(BaseModel):
    sent: bool
    reason: Literal["batch"] = "batch"
    processed_users: int
    sent_count: int


def window_to_dict(w: NotificationWindow) -> dict[str, Any]:
    return {"label": w.label, "from": w.from_, "to": w.to, "on": w.on}


# ---------------------------------------------------------------------------
# Study Plan — see docs/STUDY-PLAN-SPEC.md §API
#
# These are screen-shaped, not table-shaped. Each one answers the question its
# level of the hierarchy exists to answer, and none of them carries an internal
# item id in a user-facing string.
# ---------------------------------------------------------------------------

PlanMode = Literal["flexible", "fixed"]
PlanStatus = Literal["active", "paused", "completed", "archived"]
ItemType = Literal["learn", "practice", "retrieve"]
ItemPriority = Literal["core", "optional", "recurring"]
ItemStatus = Literal["pending", "complete", "deferred", "removed"]


class ActivePlanSummary(BaseModel):
    """Today's one line. Kept deliberately thin.

    Today asks "what should I do now?", so this carries position and the next
    study block and nothing else — no capacity, no progress paragraph, no
    description. It is also the endpoint that must never slow the due-card
    fetch, so it resolves from the plan row and one week lookup.
    """

    active: bool
    plan_id: uuid.UUID | None = None
    title: str = ""
    subject: str = ""
    week_index: int | None = None
    week_total: int | None = None
    # The current phase's concise title. Uppercased by the client's mono style,
    # not here — casing is a display decision.
    phase_title: str = ""
    # e.g. "Next Tue 19:00", or null when no study block is set.
    next_block_label: str | None = None


class PlanWeekRow(BaseModel):
    index: int
    display_title: str
    # The full guide title, always sent. The visual title may be the short one;
    # the accessible name must not be.
    full_title: str
    status: str
    is_current: bool = False


class PlanPhaseRow(BaseModel):
    index: int
    display_title: str
    full_title: str
    status: str
    week_range: str
    # `· Week 4` on the current phase only.
    current_week_label: str | None = None
    weeks: list[PlanWeekRow]


class PlanOverview(BaseModel):
    id: uuid.UUID
    title: str
    subject: str
    mode: PlanMode
    status: PlanStatus
    week_index: int
    week_total: int
    # "Est. completion · week of 19 Oct". Plan-week precision only — there is
    # deliberately no field here from which a completion day could be derived.
    forecast_label: str
    forecast_end_plan_week: int
    revision: int
    supports_recall_cards: bool
    phases: list[PlanPhaseRow]
    # One quiet line at the bottom of the map.
    latest_change: str | None = None


class WeekItemRow(BaseModel):
    id: uuid.UUID
    title: str
    estimate_minutes: int
    complete: bool
    optional: bool
    # Set when a hard prerequisite is still open, so the row can say so rather
    # than looking arbitrarily unstartable.
    blocked_by: str | None = None


class WeekSection(BaseModel):
    type: ItemType
    label: str
    aside: str
    note: str = ""
    rows: list[WeekItemRow]


class WeekDetail(BaseModel):
    plan_id: uuid.UUID
    index: int
    phase_title: str
    full_title: str
    display_title: str
    # The two facts under the title, and nothing else.
    core_complete: int
    core_total: int
    planned_minutes: int
    capacity_minutes: int
    # The two facts under the title, rendered server-side. `due_label` set this
    # rule — "computed server-side so the client never reimplements date math" —
    # and the hours rounding is the same kind of thing.
    core_line: str
    capacity_line: str
    sections: list[WeekSection]


class PracticeDebriefOut(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    item_id: uuid.UUID
    draft_text: str
    text: str
    submitted_at: datetime | None
    summary: str
    has_proposals: bool = False
    proposal_count: int = 0


class PracticeDebriefDraftIn(BaseModel):
    text: str = Field(max_length=12_000)


class PracticeDebriefSubmitIn(BaseModel):
    text: str = Field(min_length=1, max_length=12_000)


class StudyPlanResource(BaseModel):
    kind: str
    provider: str
    label: str
    action_label: str
    url: str
    language: str = ""


class StudyPlanStretchAction(BaseModel):
    title: str
    done_when: str
    minutes: int = Field(gt=0)
    resource_url: str = ""
    resource_label: str = ""


class MappedRecallCard(BaseModel):
    topic: str
    card_id: uuid.UUID | None = None
    availability_label: str


class ItemDetail(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    # The exact plan revision whose graph produced this item. Completion may
    # bind to it so curriculum content cannot change between opening the screen
    # and claiming the work as complete.
    plan_revision: int
    full_title: str
    phase_title: str
    week_index: int
    type: ItemType
    priority: ItemPriority
    status: ItemStatus
    why_it_matters: str
    done_when: str
    estimate_minutes: int
    estimate_source: str
    estimate_confidence: str
    source_excerpt: str
    source_label: str
    recall_supported: bool = False
    resources: list[StudyPlanResource] = Field(default_factory=list)
    mapped_recall_cards: list[MappedRecallCard] = Field(default_factory=list)
    stretch_actions: list[StudyPlanStretchAction] = Field(default_factory=list)
    notes: str
    study_block_label: str
    study_block_weekday: int | None
    study_block_minute_of_day: int | None
    study_block_reminder_on: bool
    completed_at: datetime | None
    reopened_at: datetime | None
    # Cards this item has already produced, and whether it may produce more.
    linked_card_ids: list[uuid.UUID] = []
    card_proposals_available: bool = False
    practice_debrief_eligible: bool = False
    practice_debrief: PracticeDebriefOut | None = None
    blocked_by: list[str] = []


class ItemEdit(BaseModel):
    full_title: str | None = Field(default=None, min_length=1, max_length=300)
    why_it_matters: str | None = None
    done_when: str | None = None
    # Enforced as a 30-minute multiple by the same CHECK constraint the importer
    # is held to, so a hand edit cannot make the displayed hours unreconcilable.
    estimate_minutes: int | None = Field(default=None, gt=0, le=2400)
    notes: str | None = None
    study_block_label: str | None = None
    study_block_weekday: int | None = Field(default=None, ge=1, le=7)
    study_block_minute_of_day: int | None = Field(default=None, ge=0, le=1439)
    study_block_reminder_on: bool | None = None

    @model_validator(mode="after")
    def _estimate_on_the_grid(self) -> "ItemEdit":
        if self.estimate_minutes is not None and self.estimate_minutes % 30:
            raise ValueError("estimates are in 30-minute increments")
        return self


class WeekPlacementOut(BaseModel):
    index: int
    capacity_minutes: int
    before_minutes: int
    after_minutes: int
    changed: bool
    over_capacity: bool


class ItemMoveOut(BaseModel):
    title: str
    from_week: int
    to_week: int
    minutes: int


class UnresolvedOut(BaseModel):
    title: str
    minutes: int
    reason: Literal["no_room", "does_not_fit", "dependency_cycle"]


class ProposalOut(BaseModel):
    """A decision screen's whole payload.

    Leads with the outcome (`headline`), then the arithmetic. Deliberately
    wordier than the map screens: length is not the constraint on a screen whose
    job is informed consent.
    """

    kind: str
    base_plan_revision: int
    headline: str
    body: str
    weeks: list[WeekPlacementOut]
    moves: list[ItemMoveOut]
    unresolved: list[UnresolvedOut]
    unresolved_minutes: int
    displaced_minutes: int
    forecast_end_plan_week: int
    forecast_label: str
    forecast_changed: bool
    # Native `disabled` on the client. An invalid proposal always carries reasons.
    can_apply: bool
    reasons: list[str]
    # What stays the same. Stated explicitly because the question the screen
    # answers is "what changes if I approve this", and the answer has two halves.
    unchanged: list[str]


class CapacityUpdate(BaseModel):
    # The week comes from the path. A body field for it was accepted and never
    # read, which reads as contract and is not.
    # Null clears the override and returns the week to the plan default.
    override_capacity_minutes: int | None = Field(default=None, gt=0, le=10080)
    base_plan_revision: int

    @model_validator(mode="after")
    def _on_the_grid(self) -> "CapacityUpdate":
        if self.override_capacity_minutes and self.override_capacity_minutes % 30:
            raise ValueError("capacity is set in 30-minute increments")
        return self


class ReplanRequest(BaseModel):
    base_plan_revision: int
    capacity_overrides: dict[int, int | None] = {}
    default_capacity_minutes: int | None = Field(default=None, gt=0, le=10080)
    deferred_item_ids: list[uuid.UUID] = []
    extra_weeks: int = Field(default=0, ge=0, le=8)
    insert_after_phase: int | None = None
    confirmed_core_removals: list[uuid.UUID] = []


class ApplyReplan(ReplanRequest):
    """Apply carries the same shape as Preview so the two cannot diverge.

    A proposal is only ever applied by recomputing it from these inputs against
    the current revision — the client never sends a placement, so there is no
    way for a stale client-side plan to be written.
    """


class PlanCreate(BaseModel):
    draft_id: uuid.UUID
    # Making this plan active pauses the current one, which is why it is an
    # explicit flag rather than a default.
    activate: bool = True


class GuidePreviewIn(BaseModel):
    guide_text: str = Field(min_length=1, max_length=400_000)
    requested_weeks: int = Field(ge=2, le=52)
    weekly_capacity_minutes: int = Field(gt=0, le=10080)
    mode: PlanMode = "flexible"
    deadline: date | None = None
    subject_hint: str = ""
    title_hint: str = ""
    start_date: date | None = None


class PreviewEdit(BaseModel):
    """Edits and review decisions applied to a draft, re-validated in place."""

    estimates_reviewed: list[str] | None = None
    omissions_acknowledged: bool | None = None
    retrieval_approved: list[str] | None = None
    retrieval_rejected: list[str] | None = None
    dependencies_confirmed: list[str] | None = None
    item_estimates: dict[str, int] = {}
    overview_titles: dict[str, str] = {}


class CheckRow(BaseModel):
    key: str
    label: str
    status: Literal["ok", "needs_review", "blocked"]
    value: str
    detail: str = ""
    # Only exceptions carry a destination, and only rows with a destination get
    # a disclosure arrow.
    destination: str | None = None


class PreviewPhase(BaseModel):
    index: int
    display_title: str
    full_title: str
    description: str
    week_range: str


class PreviewOut(BaseModel):
    draft_id: uuid.UUID
    status: Literal["pending", "ready", "failed"]
    title: str
    subject: str
    mode: PlanMode
    weeks: int
    phases: int
    weekly_capacity_minutes: int
    total_minutes: int
    forecast_label: str
    supports_recall_cards: bool
    checks: list[CheckRow]
    can_create: bool
    phase_rows: list[PreviewPhase] = []
    # Populated on a failed import. The guide, duration, capacity, mode, deadline
    # and every edit are still on the draft, so the retry loses nothing.
    error: str = ""


class PlanListEntry(BaseModel):
    id: uuid.UUID
    title: str
    subject: str
    status: PlanStatus
    meta: str
    created_at: datetime


class PlanList(BaseModel):
    active: list[PlanListEntry]
    paused: list[PlanListEntry]
    completed: list[PlanListEntry]
    archived: list[PlanListEntry]


class RevisionOut(BaseModel):
    id: uuid.UUID
    kind: str
    summary: str
    created_at: datetime
    reversible: bool


class PlanRecap(BaseModel):
    """The completed-plan screen. Plan work only.

    Estimated, not measured — Study Plan does not track actual study time — and
    global reviews are deliberately absent, because they were never part of this
    plan's capacity.
    """

    id: uuid.UUID
    title: str
    core_complete: int
    core_total: int
    optional_deferred: int
    estimated_minutes: int
    learn_practice_minutes: int
    retrieval_minutes: int
    retrieval_completed: int
    practice_completed: int
    remaining_gaps: list[str]


class GateResult(BaseModel):
    question_index: int
    question: str
    passed: bool
    reason: str


class CardProposalOut(BaseModel):
    id: uuid.UUID
    revision: int
    topic: str
    category: str
    canonical_question: str
    reason: str
    gate: list[GateResult]
    # `suggested` is the only disposition that is selectable and counted.
    disposition: Literal[
        "suggested", "not_suggested", "existing", "possible_overlap", "accepted", "skipped"
    ]
    duplicate_check_result: Literal["none", "exact", "possible"]
    existing_card_id: uuid.UUID | None = None
    existing_card_context: str = ""
    # The one failed question, for the NOT SUGGESTED row. Null when all five passed.
    failed_question_index: int | None = None
    failed_reason: str = ""


class CardProposalList(BaseModel):
    plan_id: uuid.UUID
    item_id: uuid.UUID
    supports_recall_cards: bool
    suggested_count: int
    proposals: list[CardProposalOut]
    note: str


class CardAcceptIn(BaseModel):
    selected_proposal_ids: list[uuid.UUID] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8, max_length=200)
    proposal_revision: int
    edits: dict[str, dict[str, str]] = {}


class CardAcceptOut(BaseModel):
    status: Literal["committed"]
    created_card_ids: list[uuid.UUID]
    # True when this response replayed an earlier commit rather than creating
    # anything. The UI shows ADDED either way — both mean the cards exist.
    replayed: bool = False


class DuplicateResolution(BaseModel):
    proposal_id: uuid.UUID
    action: Literal["keep_new", "use_existing", "skip"]


# Public study material -------------------------------------------------------


MaterialSourceKind = Literal[
    "guide", "article", "documentation", "course", "book", "notes", "other"
]
LessonRecallLevel = Literal[
    "definition_recognition",
    "mechanism",
    "derivation",
    "application",
    "failure_tradeoff",
]
ContentProvenance = Literal[
    "legacy_unspecified",
    "exact_source_excerpt",
    "learner_notes",
    "coached_correction",
    "ai_derived_summary",
]


class LessonRecallPrompt(BaseModel):
    level: LessonRecallLevel
    question: str = Field(min_length=1, max_length=2000)


class MaterialImportIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_text: str = Field(default="", max_length=400_000)
    # Provenance only. The server never fetches this URL; extraction always uses
    # the required pasted source_text above.
    source_url: str = Field(default="", max_length=4000)
    # Classification of the pasted content, separate from kind and source_url.
    # The legacy value keeps older clients readable but cannot confirm a lesson.
    content_provenance: ContentProvenance = "legacy_unspecified"
    kind: MaterialSourceKind = "guide"
    original_filename: str = Field(default="", max_length=255)
    mime_type: Literal["text/plain", "text/markdown", "application/pdf"] = "text/plain"
    import_path: Literal["topics", "plan", "lesson"] = "topics"
    intent: Literal["already_studied", "learn"] = "already_studied"
    requested_weeks: int = Field(default=12, ge=2, le=52)
    weekly_capacity_minutes: int = Field(default=480, gt=0, le=10080)
    mode: PlanMode = "flexible"
    deadline: date | None = None
    previous_version_id: uuid.UUID | None = None

    @field_validator("source_url")
    @classmethod
    def source_url_is_safe_provenance(cls, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        parsed = urlsplit(text)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(character.isspace() for character in text)
            or any(character in text for character in "<>")
        ):
            raise ValueError(
                "source_url must be an absolute http(s) URL without credentials"
            )
        return text

    @model_validator(mode="after")
    def pasted_text_is_the_required_authority(self) -> "MaterialImportIn":
        if len(self.source_text.strip()) < 200:
            raise ValueError(
                "at least 200 readable characters of pasted source_text are required "
                "for processing; source_url is provenance metadata only and the "
                "server never fetches it"
            )
        return self


class MaterialTopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    section_title: str
    topic: str
    answer_anchor: str
    source_excerpt: str
    canonical_question: str
    answer_rubric: dict[str, str]
    recall_questions: list[LessonRecallPrompt]
    card_id: uuid.UUID | None
    status: Literal["clean", "needs_attention", "excluded", "confirmed"]
    issue: str


LessonCheckKind = Literal["formation", "transfer"]
LessonCheckCondition = Literal["attempt_first", "restudy"]
LessonCheckStatus = Literal["open", "submitted", "exposed"]
LessonCheckOutcome = Literal[
    "accurate_account",
    "missing_mechanism",
    "misconception",
    "missing_boundary",
    "insufficient_evidence",
]


class MaterialTopicPreviewOut(BaseModel):
    """Pilot-safe proposal preview: deliberately contains no answer authority."""

    id: uuid.UUID
    position: int
    section_title: str
    topic: str
    formation_question: str | None = None
    status: Literal["clean", "needs_attention", "excluded", "confirmed"]
    issue: str
    formation_state: Literal[
        "not_started", "open", "submitted", "exposed", "unavailable"
    ]
    transfer_state: Literal[
        "unavailable", "locked", "available", "submitted", "debriefed"
    ] = "unavailable"


class MaterialImportPreviewOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    source_url: str
    content_provenance: ContentProvenance
    status: Literal[
        "draft",
        "pending",
        "processing",
        "ready",
        "needs_attention",
        "failed",
        "confirmed",
        "superseded",
    ]
    import_path: Literal["topics", "plan", "lesson"]
    intent: Literal["already_studied", "learn"]
    clean_count: int
    attention_count: int
    error: str
    lesson_grounding_required: bool = False
    proposals_ready_at: datetime | None = None
    review_opened_at: datetime | None = None
    confirmed_at: datetime | None = None
    topics: list[MaterialTopicPreviewOut] = Field(default_factory=list)


class LessonCheckDraftIn(BaseModel):
    draft_text: str = Field(default="", max_length=20_000)


class LessonCheckSubmitIn(BaseModel):
    answer_text: str = Field(min_length=1, max_length=20_000)

    @field_validator("answer_text")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("answer_text must not be blank")
        return normalized


class LessonCheckOut(BaseModel):
    """Resumable check state with no correction or answer authority."""

    id: uuid.UUID
    proposal_id: uuid.UUID
    card_id: uuid.UUID | None = None
    kind: LessonCheckKind
    condition: LessonCheckCondition | None = None
    prompt_level: Literal["canonical", "application", "failure_tradeoff"]
    prompt_version: str
    prompt_text: str
    status: LessonCheckStatus
    draft_text: str
    qualitative_outcome: LessonCheckOutcome | None = None
    has_feedback: bool = False
    exposed_at: datetime | None = None
    recall_not_before_at: datetime | None = None
    available_at: datetime | None = None
    started_at: datetime
    submitted_at: datetime | None = None
    updated_at: datetime


class MaterialTopicAuthorityOut(BaseModel):
    """Authority-bearing response returned only after an exposure commit."""

    check: LessonCheckOut
    proposal_id: uuid.UUID
    topic: str
    section_title: str
    source_title: str
    source_url: str
    content_provenance: ContentProvenance
    source_excerpt: str
    answer_basis: str
    canonical_question: str
    answer_rubric: dict[str, str]
    recall_questions: list[LessonRecallPrompt]
    feedback: str
    exposed_at: datetime
    recall_not_before_at: datetime
    confirmation_title: str
    confirmation_message: str


class MaterialImportOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    source_url: str
    content_provenance: ContentProvenance
    version: int
    status: Literal[
        "draft",
        "pending",
        "processing",
        "ready",
        "needs_attention",
        "failed",
        "confirmed",
        "superseded",
    ]
    import_path: Literal["topics", "plan", "lesson"]
    intent: Literal["already_studied", "learn"]
    original_filename: str
    character_count: int
    clean_count: int
    attention_count: int
    error: str
    plan_draft_id: uuid.UUID | None = None
    comparison: dict[str, int] = Field(default_factory=dict)
    topics: list[MaterialTopicOut] = Field(default_factory=list)
    # Additive rollout signal for lesson previews that have not completed the
    # current independent source-grounding gate, including a failed recovery
    # pass. These previews cannot be confirmed; the source can be retried in place.
    lesson_grounding_required: bool = False
    artifacts_ready: bool = False
    distilled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MaterialTopicEdit(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    answer_anchor: str | None = Field(default=None, min_length=1, max_length=4000)
    action: Literal["keep", "exclude", "merge"] = "keep"
    merge_into_id: uuid.UUID | None = None


class MaterialConfirmIn(BaseModel):
    # Pilot exclusion may intentionally confirm a source with zero kept concepts.
    # The router retains the legacy nonpilot requirement of at least one.
    selected_topic_ids: list[uuid.UUID] = Field(default_factory=list)
    # Lets an upgraded client classify an already-imported legacy lesson at the
    # final review boundary without re-uploading or reprocessing its source.
    content_provenance: ContentProvenance | None = None


class MaterialConfirmOut(BaseModel):
    source_id: uuid.UUID
    created_card_ids: list[uuid.UUID]


class LessonConceptProgress(BaseModel):
    proposal_id: uuid.UUID
    card_id: uuid.UUID
    concept: str
    mastery_summary: str
    last_score: int | None
    recall_score: int | None
    score_kind: ScoreKind
    scoring_contract_version: ScoringContractVersion | None
    last_reviewed_at: datetime | None
    next_review_at: date
    interval_days: int


class LessonProgressOut(BaseModel):
    source_id: uuid.UUID
    title: str
    concept_count: int
    reviewed_count: int
    weak_count: int
    complete: bool
    next_card_id: uuid.UUID | None
    concepts: list[LessonConceptProgress]


class LessonQuizResult(BaseModel):
    session_id: uuid.UUID
    reviewed_at: datetime
    question: str
    recall_score: int | None
    scoring_contract_version: ScoringContractVersion
    scored_follow_up_used: bool
    graded_summary: str
    feedback: str


class LearningNoteConcept(BaseModel):
    proposal_id: uuid.UUID
    card_id: uuid.UUID
    concept: str
    canonical_question: str
    answer_rubric: dict[str, str]
    source_title: str
    source_url: str
    mental_model: str
    how_it_works: str
    gotchas: list[str]
    recall_prompts: list[LessonRecallPrompt]
    quiz_results: list[LessonQuizResult]
    confidence: Literal["unrated", "needs_review", "developing", "established"]


class LearningWritebackSource(BaseModel):
    id: str
    lineage_id: str
    version: int = Field(ge=1)
    title: str
    url: str
    distilled_at: datetime


class LearningWritebackAnswerRubric(BaseModel):
    mechanism: str
    acceptable_alternative: str
    trade_off: str
    failure_mode: str
    misconception: str

    model_config = ConfigDict(extra="forbid")


class LearningWritebackCandidate(BaseModel):
    id: str
    type: Literal[
        "definition_recognition",
        "mechanism",
        "derivation",
        "application",
        "failure_tradeoff",
    ]
    prompt: str
    answer_rubric: str


class LearningWritebackEvidence(BaseModel):
    id: str
    reviewed_at: datetime
    prompt: str
    score: int = Field(ge=0, le=5)
    graded_summary: str
    scoring_contract_version: ScoringContractVersion
    scored_follow_up_used: bool


class LearningWritebackConcept(BaseModel):
    id: str
    card_id: str
    title: str
    answer_rubric: LearningWritebackAnswerRubric
    mental_model: str
    how_it_works: str
    gotchas: list[str] = Field(min_length=1, max_length=8)
    recall_candidates: list[LearningWritebackCandidate] = Field(
        min_length=5, max_length=5
    )
    quiz_evidence: list[LearningWritebackEvidence] = Field(min_length=1, max_length=20)
    producer_assessment: Literal[
        "unrated", "needs_review", "developing", "established"
    ]


class LearningWritebackBundle(BaseModel):
    schema_: Literal["second-brain.learning-writeback"] = Field(alias="schema")
    schema_version: Literal[1]
    producer: Literal["devmax"]
    source: LearningWritebackSource
    concepts: list[LearningWritebackConcept] = Field(min_length=1)
    export_id: str

    model_config = ConfigDict(populate_by_name=True)


class MaterialArtifactsOut(BaseModel):
    source_id: uuid.UUID
    title: str
    source_url: str
    # Kept outside the strict v1 writeback bundle until producer and importer can
    # move to v2 together. This field is available to API/export clients now.
    content_provenance: ContentProvenance
    distilled_at: datetime
    canonical_note_markdown: str
    recall_export_markdown: str
    concepts: list[LearningNoteConcept]
    writeback_bundle: LearningWritebackBundle


class ManualTopicIn(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    answer_anchor: str = Field(min_length=1, max_length=4000)


class ManualMaterialIn(BaseModel):
    title: str = Field(default="My topics", min_length=1, max_length=200)
    topics: list[ManualTopicIn] = Field(min_length=1, max_length=50)


class CollectionSummary(BaseModel):
    id: str
    title: str
    subtitle: str
    version: str
    topic_count: int
    available: bool = True


class CollectionDetail(CollectionSummary):
    sections: list[str]
    source_note: str
    topics: list[ManualTopicIn]


class AccountExport(BaseModel):
    exported_at: datetime
    account: dict[str, object]
    settings: dict[str, object]
    sources: list[dict[str, object]]
    cards: list[dict[str, object]]
    sessions: list[dict[str, object]]
    study_plans: list[dict[str, object]]
    ai_consent_events: list[dict[str, object]]
    llm_usage: list[dict[str, object]]
    # This is the private, user-requested account export. These records remain
    # excluded from second-brain writeback and aggregate pilot reports.
    lesson_checks: list[dict[str, object]]
    lesson_proposal_audits: list[dict[str, object]]
    study_pilot_enrollments: list[dict[str, object]]
    study_pilot_assignments: list[dict[str, object]]
