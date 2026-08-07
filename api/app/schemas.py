import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DueCard(BaseModel):
    id: uuid.UUID
    topic: str
    category: str
    mastery_summary: str
    last_score: int | None
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
    # The three axes behind `last_score`. Coverage's rollup means these across the
    # library; nothing else consumes them.
    last_accuracy: int | None = None
    last_depth: int | None = None
    last_boundaries: int | None = None
    ease_factor: float
    interval_days: int
    repetitions: int
    next_review_at: date
    # Both computed server-side so the client never reimplements date math.
    due_label: str
    days_since_review: int | None
    missed_count: int


class Turn(BaseModel):
    role: Literal["question", "answer", "follow_up", "score"]
    text: str


class SessionHistory(BaseModel):
    id: uuid.UUID
    date: datetime
    score: int | None
    feedback: str
    turns: list[Turn]


class CardDetail(CardSummary):
    sessions: list[SessionHistory]


class TierCard(BaseModel):
    id: uuid.UUID
    topic: str
    mastery_summary: str
    last_score: int | None = None
    days_overdue: int | None = None


class Overview(BaseModel):
    counts: dict[str, int]
    shaky: list[TierCard]
    cold: list[TierCard]


class CreateCard(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    schedule: Literal["now", "next"] = "now"


class SessionStart(BaseModel):
    session_id: uuid.UUID
    question: str
    is_follow_up: bool
    draft_text: str
    resumed: bool


class DraftUpdate(BaseModel):
    draft_text: str = ""


class AnswerIn(BaseModel):
    text: str = ""


class FollowUpOut(BaseModel):
    status: Literal["follow_up"] = "follow_up"
    question: str


class CompleteOut(BaseModel):
    status: Literal["complete"] = "complete"
    score: int
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


class ReattemptOut(BaseModel):
    """Deliberately carries no score.

    Turn 3's `accuracy` is stored but never returned — the app shows one
    numeral per session and that numeral is turn 2's composite. Sending a second
    number to a client that has no place to put it invites putting it somewhere.
    """

    mastery_summary: str


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


class NotificationWindow(BaseModel):
    label: str
    on: bool
    # `from` is a Python keyword, so the field is aliased to match the wire format.
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class SettingsOut(BaseModel):
    reviews_per_day: int = Field(ge=1, le=6)
    timezone: str
    windows: list[NotificationWindow]


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


class SettingsIn(SettingsOut):
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


class ItemDetail(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
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


class MaterialImportIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_text: str = Field(min_length=200, max_length=400_000)
    original_filename: str = Field(default="", max_length=255)
    mime_type: Literal["text/plain", "text/markdown", "application/pdf"] = "text/plain"
    import_path: Literal["topics", "plan"] = "topics"
    intent: Literal["already_studied", "learn"] = "already_studied"
    requested_weeks: int = Field(default=12, ge=2, le=52)
    weekly_capacity_minutes: int = Field(default=480, gt=0, le=10080)
    mode: PlanMode = "flexible"
    deadline: date | None = None
    previous_version_id: uuid.UUID | None = None


class MaterialTopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    section_title: str
    topic: str
    answer_anchor: str
    source_excerpt: str
    status: Literal["clean", "needs_attention", "excluded", "confirmed"]
    issue: str


class MaterialImportOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
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
    import_path: Literal["topics", "plan"]
    intent: Literal["already_studied", "learn"]
    original_filename: str
    character_count: int
    clean_count: int
    attention_count: int
    error: str
    plan_draft_id: uuid.UUID | None = None
    comparison: dict[str, int] = Field(default_factory=dict)
    topics: list[MaterialTopicOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MaterialTopicEdit(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    answer_anchor: str | None = Field(default=None, min_length=1, max_length=4000)
    action: Literal["keep", "exclude", "merge"] = "keep"
    merge_into_id: uuid.UUID | None = None


class MaterialConfirmIn(BaseModel):
    selected_topic_ids: list[uuid.UUID] = Field(min_length=1)


class MaterialConfirmOut(BaseModel):
    source_id: uuid.UUID
    created_card_ids: list[uuid.UUID]


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
