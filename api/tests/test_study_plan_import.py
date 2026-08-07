"""The gate between the importer and the database.

Every test here is about not trusting the model: the arithmetic is recomputed,
the source offsets are checked against the verbatim guide, and a response that
cannot be reconciled produces a blocked check rather than a plan.
"""

from datetime import date

import pytest

from app.services.study_plan_import import (
    CHECK_BLOCKED,
    CHECK_NEEDS_REVIEW,
    CHECK_OK,
    ImportError_,
    build_plan_rows,
    default_start_date,
    overview_title_problem,
    validate_import,
    validate_request,
)

START = date(2026, 7, 27)

GUIDE = (
    "Week 1: The request path. Trace a request from DNS to the database.\n"
    "Week 2: Storage engines and transactions. B-trees, LSM trees, isolation.\n"
    "Week 3: Caching, sharding, consistent hashing, and CAP.\n"
    "Prerequisite: finish consistent hashing before sharding strategies.\n"
) * 4


def excerpt_at(start: int, end: int) -> str:
    return GUIDE[start:end]


def _item(key: str, week: int, order: int, **overrides) -> dict:
    span = (0, 20)
    defaults = {
        "key": key,
        "week_index": week,
        "guide_order": order,
        "type": "learn",
        "priority": "core",
        "full_title": f"Item {key}",
        "why_it_matters": "Because.",
        "done_when": "You can explain it closed-book.",
        "estimate_minutes": 60,
        "estimate_source": "imported",
        "estimate_confidence": "high",
        "origin": "imported",
        "source_item_key": None,
        "source_start": span[0],
        "source_end": span[1],
        "source_excerpt": excerpt_at(*span),
        "parser_interpretation": "",
    }
    return {**defaults, **overrides}


def raw_response(**overrides) -> dict:
    phases = [
        {
            "index": 1,
            "full_title": "Foundations of the request path",
            "overview_title": "Request foundations",
            "description": "Build the concepts every later design uses.",
        },
        {
            "index": 2,
            "full_title": "Concrete technologies",
            "overview_title": "Technologies",
            "description": "See how systems implement those foundations.",
        },
    ]
    weeks = [
        {
            "index": 1,
            "phase_index": 1,
            "full_title": "The request path",
            "overview_title": "Request path",
        },
        {
            "index": 2,
            "phase_index": 1,
            "full_title": "Storage engines and transactions",
            "overview_title": "Storage engines",
        },
        {
            "index": 3,
            "phase_index": 2,
            "full_title": "Relational and NoSQL systems",
            "overview_title": "Databases",
        },
        {
            "index": 4,
            "phase_index": 2,
            "full_title": "Caching, sharding, and CAP",
            "overview_title": "Caching and sharding",
        },
    ]
    items = [
        _item("L1", 1, 1),
        _item("L2", 2, 2),
        _item("L3", 3, 3),
        _item("L4", 4, 4),
    ]
    base = {
        "subject": "Senior backend interview",
        "subject_slug": "system-design",
        "supports_technical_recall_cards": True,
        "plan_title": "Senior backend interview",
        "phases": phases,
        "weeks": weeks,
        "items": items,
        "dependencies": [],
        "unresolved_estimates": [],
        "possible_omissions": [],
    }
    return {**base, **overrides}


def run(raw=None, **kwargs):
    defaults = dict(
        guide_text=GUIDE,
        requested_weeks=4,
        weekly_capacity_minutes=720,
        mode="flexible",
        deadline=None,
        start_date=START,
    )
    return validate_import(raw or raw_response(), **{**defaults, **kwargs})


def check(result, key):
    return next(c for c in result.checks if c.key == key)


# --- request validation -----------------------------------------------------


def test_a_guide_too_short_to_plan_is_rejected_before_a_paid_call():
    errors = validate_request(
        guide_text="too short",
        requested_weeks=12,
        weekly_capacity_minutes=720,
        mode="flexible",
        deadline=None,
        start_date=START,
    )
    assert any("characters" in e for e in errors)


def test_a_fixed_plan_needs_a_deadline_and_a_flexible_one_must_not_have_it():
    fixed = validate_request(
        guide_text=GUIDE,
        requested_weeks=12,
        weekly_capacity_minutes=720,
        mode="fixed",
        deadline=None,
        start_date=START,
    )
    assert "a fixed plan needs a deadline" in fixed

    flexible = validate_request(
        guide_text=GUIDE,
        requested_weeks=12,
        weekly_capacity_minutes=720,
        mode="flexible",
        deadline=date(2026, 10, 18),
        start_date=START,
    )
    assert "a flexible plan cannot carry a deadline" in flexible


def test_capacity_must_be_a_positive_multiple_of_thirty_minutes():
    errors = validate_request(
        guide_text=GUIDE,
        requested_weeks=12,
        weekly_capacity_minutes=725,
        mode="flexible",
        deadline=None,
        start_date=START,
    )
    assert any("multiple of 30" in e for e in errors)


def test_a_plan_starts_on_the_monday_of_the_current_week():
    assert default_start_date(date(2026, 7, 30)) == date(2026, 7, 27)
    assert default_start_date(date(2026, 7, 27)) == date(2026, 7, 27)


# --- structure --------------------------------------------------------------


def test_a_clean_import_passes_every_check():
    result = run()
    assert result.can_create
    assert {c.status for c in result.checks} == {CHECK_OK}
    assert result.preview["guide_text"] == GUIDE


def test_a_week_count_that_disagrees_with_the_request_blocks_creation():
    result = run(requested_weeks=12)
    assert check(result, "duration").status == CHECK_BLOCKED
    assert not result.can_create


def test_gaps_in_the_week_numbering_are_unusable_rather_than_a_check():
    raw = raw_response()
    raw["weeks"][3]["index"] = 9
    with pytest.raises(ImportError_, match="1..N"):
        run(raw)


def test_a_week_pointing_at_a_phase_that_does_not_exist_is_unusable():
    raw = raw_response()
    raw["weeks"][0]["phase_index"] = 7
    with pytest.raises(ImportError_, match="does not exist"):
        run(raw)


def test_phases_must_own_contiguous_runs_of_weeks():
    raw = raw_response()
    raw["weeks"][2]["phase_index"] = 1  # phase 1, 1, 1, 2 is fine
    run(raw)
    raw["weeks"][1]["phase_index"] = 2  # now 1, 2, 1, 2 — interleaved
    with pytest.raises(ImportError_, match="non-contiguous"):
        run(raw)


def test_a_duplicate_item_key_is_unusable():
    raw = raw_response()
    raw["items"].append(_item("L1", 4, 9))
    with pytest.raises(ImportError_, match="more than once"):
        run(raw)


def test_an_item_in_a_week_that_does_not_exist_is_unusable():
    raw = raw_response()
    raw["items"][0]["week_index"] = 11
    with pytest.raises(ImportError_, match="does not exist"):
        run(raw)


def test_a_plan_with_no_core_work_is_blocked():
    raw = raw_response()
    for item in raw["items"]:
        item["priority"] = "optional"
    result = run(raw)
    assert check(result, "core").status == CHECK_BLOCKED
    assert not result.can_create


# --- arithmetic -------------------------------------------------------------


def test_weekly_capacity_is_recomputed_from_the_item_estimates():
    raw = raw_response()
    raw["items"].append(_item("BIG", 1, 5, estimate_minutes=720))
    result = run(raw)

    capacity = check(result, "capacity")
    assert capacity.status == CHECK_NEEDS_REVIEW
    assert capacity.value == "1 week over"
    assert "Week 1" in capacity.detail
    assert not result.can_create


def test_an_estimate_off_the_thirty_minute_grid_is_rounded_up_and_flagged():
    raw = raw_response()
    raw["items"][0]["estimate_minutes"] = 45
    result = run(raw)

    stored = next(i for i in result.preview["items"] if i["key"] == "L1")
    assert stored["estimate_minutes"] == 60
    assert stored["estimate_minutes_raw"] == 45
    assert check(result, "estimates").status == CHECK_NEEDS_REVIEW


def test_the_preview_total_is_the_sum_of_its_items():
    result = run()
    assert result.preview["total_minutes"] == sum(
        i["estimate_minutes"] for i in result.preview["items"]
    )
    assert result.preview["total_minutes"] == 240


def test_a_low_confidence_estimate_needs_review_until_the_user_says_otherwise():
    raw = raw_response()
    raw["items"][0]["estimate_confidence"] = "needs_review"
    assert check(run(raw), "estimates").status == CHECK_NEEDS_REVIEW

    resolved = run(raw, resolutions={"estimates_reviewed": ["L1"]})
    assert check(resolved, "estimates").status == CHECK_OK
    assert resolved.can_create


# --- provenance -------------------------------------------------------------


def test_a_wrong_offset_is_recomputed_from_the_excerpt():
    """The excerpt is the source of truth; the model's character count is not.

    In a live run against `docs/CURRICULUM.md` the model quoted the guide verbatim
    but miscounted the offsets on 31 of 72 items. Treating that as a provenance
    failure would have blocked a perfectly good import.
    """
    raw = raw_response()
    raw["items"][0]["source_start"] = 500
    raw["items"][0]["source_end"] = 520
    result = run(raw)

    stored = next(i for i in result.preview["items"] if i["key"] == "L1")
    assert stored["source_start"] == 0
    # The invariant that matters: the stored span and the stored quote are the
    # same piece of the guide, so "jump to source" lands on what was shown.
    assert GUIDE[stored["source_start"] : stored["source_end"]].strip() == stored["source_excerpt"]
    assert check(result, "coverage").status == CHECK_OK


def test_an_offset_past_the_end_of_the_guide_is_recovered_too():
    raw = raw_response()
    raw["items"][0]["source_start"] = 10
    raw["items"][0]["source_end"] = 10_000_000
    result = run(raw)

    stored = next(i for i in result.preview["items"] if i["key"] == "L1")
    assert stored["source_start"] == 0
    assert GUIDE[stored["source_start"] : stored["source_end"]].strip() == stored["source_excerpt"]


def test_an_excerpt_that_appears_nowhere_in_the_guide_is_a_real_failure():
    """This one means the item was invented rather than read, and it blocks."""
    raw = raw_response()
    raw["items"][0]["source_excerpt"] = "Week 9: Kubernetes operators and CRDs."
    raw["items"][0]["source_start"] = 0
    raw["items"][0]["source_end"] = 38
    result = run(raw)

    stored = next(i for i in result.preview["items"] if i["key"] == "L1")
    assert stored["source_start"] is None
    coverage = check(result, "coverage")
    assert coverage.status == CHECK_NEEDS_REVIEW
    assert coverage.item_keys == ["L1"]


def test_an_excerpt_reflowed_across_lines_still_resolves():
    raw = raw_response()
    raw["items"][0]["source_start"] = None
    raw["items"][0]["source_end"] = None
    raw["items"][0]["source_excerpt"] = "Week  1:\n  The   request path."
    result = run(raw)

    stored = next(i for i in result.preview["items"] if i["key"] == "L1")
    assert stored["source_start"] == 0
    assert check(result, "coverage").status == CHECK_OK


def test_an_honestly_missing_span_is_fine():
    raw = raw_response()
    raw["items"][0].update(source_start=None, source_end=None, source_excerpt="")
    assert check(run(raw), "coverage").status == CHECK_OK


def test_offsets_are_compared_with_whitespace_collapsed():
    """A line wrap in the excerpt is not a provenance failure."""
    raw = raw_response()
    raw["items"][0]["source_start"] = 0
    raw["items"][0]["source_end"] = 20
    raw["items"][0]["source_excerpt"] = GUIDE[0:20].replace(" ", "  ")
    assert check(run(raw), "coverage").status == CHECK_OK


def test_a_possible_omission_must_be_acknowledged_before_creation():
    raw = raw_response(
        possible_omissions=[{"note": "A behavioral section", "source_excerpt": "..."}]
    )
    assert check(run(raw), "coverage").status == CHECK_NEEDS_REVIEW

    acknowledged = run(raw, resolutions={"omissions_acknowledged": True})
    assert check(acknowledged, "coverage").status == CHECK_OK


# --- retrieval --------------------------------------------------------------


def test_generated_retrieval_must_be_approved_and_is_dropped_when_it_is_not():
    raw = raw_response()
    raw["items"].append(
        _item(
            "R1",
            1,
            9,
            type="retrieve",
            priority="recurring",
            origin="generated",
            source_item_key="L1",
            estimate_minutes=30,
        )
    )
    unapproved = run(raw)
    assert check(unapproved, "retrieval").status == CHECK_NEEDS_REVIEW
    assert not unapproved.can_create

    approved = run(raw, resolutions={"retrieval_approved": ["R1"]})
    assert check(approved, "retrieval").status == CHECK_OK

    rows = build_plan_rows(approved.preview, start_date=START)
    assert any(i.full_title == "Item R1" for i in rows["items"])

    rejected = run(raw, resolutions={"retrieval_rejected": ["R1"]})
    rows = build_plan_rows(rejected.preview, start_date=START)
    assert not any(i.full_title == "Item R1" for i in rows["items"])


def test_imported_retrieval_needs_no_approval():
    raw = raw_response()
    raw["items"].append(
        _item("R1", 1, 9, type="retrieve", priority="recurring", estimate_minutes=30)
    )
    assert check(run(raw), "retrieval").status == CHECK_OK


def test_a_retrieval_pointing_at_a_missing_source_keeps_the_activity():
    raw = raw_response()
    raw["items"].append(
        _item(
            "R1",
            1,
            9,
            type="retrieve",
            priority="recurring",
            source_item_key="GHOST",
            estimate_minutes=30,
        )
    )
    result = run(raw)
    stored = next(i for i in result.preview["items"] if i["key"] == "R1")
    assert stored["source_item_key"] is None
    assert "not found" in stored["parser_interpretation"]


# --- dependencies -----------------------------------------------------------


def test_strong_imported_ordering_is_accepted_without_asking():
    raw = raw_response(
        dependencies=[
            {
                "prerequisite_key": "L1",
                "dependent_key": "L2",
                "kind": "hard",
                "source": "imported",
                "confidence": "high",
                "rationale": "Explicit prerequisite language.",
                "source_excerpt": "Prerequisite: finish consistent hashing",
            }
        ]
    )
    dependencies = check(run(raw), "dependencies")
    assert dependencies.status == CHECK_OK
    assert dependencies.value == "Confirmed"


def test_an_inferred_dependency_reaches_the_audit_until_confirmed():
    raw = raw_response(
        dependencies=[
            {
                "prerequisite_key": "L1",
                "dependent_key": "L2",
                "kind": "hard",
                "source": "inferred",
                "confidence": "medium",
                "rationale": "The guide appears to order these.",
                "source_excerpt": "",
            }
        ]
    )
    assert check(run(raw), "dependencies").status == CHECK_NEEDS_REVIEW

    confirmed = run(raw, resolutions={"dependencies_confirmed": ["L1->L2"]})
    assert check(confirmed, "dependencies").status == CHECK_OK

    rows = build_plan_rows(confirmed.preview, start_date=START)
    assert rows["dependencies"][0].confirmed_at is not None


def test_a_dependency_naming_an_unknown_item_is_dropped():
    raw = raw_response(
        dependencies=[
            {
                "prerequisite_key": "GHOST",
                "dependent_key": "L2",
                "kind": "hard",
                "source": "imported",
                "confidence": "high",
                "rationale": "",
                "source_excerpt": "",
            }
        ]
    )
    assert run(raw).preview["dependencies"] == []


def test_a_self_dependency_is_dropped():
    raw = raw_response(
        dependencies=[
            {
                "prerequisite_key": "L1",
                "dependent_key": "L1",
                "kind": "hard",
                "source": "imported",
                "confidence": "high",
                "rationale": "",
                "source_excerpt": "",
            }
        ]
    )
    assert run(raw).preview["dependencies"] == []


# --- overview titles --------------------------------------------------------


@pytest.mark.parametrize(
    "title, fragment",
    [
        ("", "empty"),
        ("Coordination, stream processing, and approximate structures", "at most 5"),
        ("Coordination and…", "truncation"),
        ("Coordination and ...", "truncation"),
        # Vague, not short: this is rule 7, and it is why one-word titles are fine
        # in general but these two are not.
        ("Advanced", "too vague"),
        ("Systems", "too vague"),
        ("Interprocess synchronisation primitives", "characters"),
    ],
)
def test_unusable_concise_titles_are_named_rather_than_silently_accepted(title, fragment):
    problem = overview_title_problem(title)
    assert problem is not None
    assert fragment in problem


@pytest.mark.parametrize(
    "title",
    [
        # Every worked example from V3.5 §4, including the single-word ones.
        "Databases",
        "Streaming & search",
        "Coordination",
        "Caching & sharding",
        "Cardiac conduction",
        "Filtration",
        "Acid–base",
        "Judicial review",
    ],
)
def test_the_handoffs_own_worked_examples_all_pass(title):
    assert overview_title_problem(title) is None


def test_two_weeks_in_a_phase_cannot_share_a_short_title():
    raw = raw_response()
    raw["weeks"][0]["overview_title"] = "Storage engines"  # same as week 2, phase 1
    titles = check(run(raw), "titles")
    assert titles.status == CHECK_NEEDS_REVIEW
    assert "two weeks called" in titles.detail


# --- subject eligibility ----------------------------------------------------


def test_a_technical_subject_the_importer_confirms_can_propose_cards():
    assert run().preview["supports_recall_cards"] is True


def test_an_unsupported_subject_cannot_propose_cards_however_the_model_answers():
    raw = raw_response(
        subject="Human anatomy",
        subject_slug="anatomy",
        supports_technical_recall_cards=True,
    )
    assert run(raw).preview["supports_recall_cards"] is False


def test_a_technical_slug_the_importer_calls_unsupported_still_cannot():
    raw = raw_response(supports_technical_recall_cards=False)
    assert run(raw).preview["supports_recall_cards"] is False


# --- deadlines --------------------------------------------------------------


def test_a_fixed_plan_that_ends_before_its_deadline_passes():
    result = run(mode="fixed", deadline=date(2026, 9, 30))
    assert check(result, "deadline").status == CHECK_OK


def test_a_fixed_plan_that_ends_after_its_deadline_needs_a_decision():
    result = run(mode="fixed", deadline=date(2026, 8, 10))
    deadline = check(result, "deadline")
    assert deadline.status == CHECK_NEEDS_REVIEW
    assert "late" in deadline.detail
    assert not result.can_create


# --- preview to rows --------------------------------------------------------


def test_building_rows_preserves_structure_and_provenance():
    result = run()
    rows = build_plan_rows(result.preview, start_date=START)

    assert rows["plan"].guide_text == GUIDE
    assert rows["plan"].start_date == START
    assert rows["plan"].forecast_end_plan_week == 4
    assert len(rows["phases"]) == 2
    assert len(rows["weeks"]) == 4
    assert len(rows["items"]) == 4

    week_ids = {w.id for w in rows["weeks"]}
    phase_ids = {p.id for p in rows["phases"]}
    for item in rows["items"]:
        assert item.week_id in week_ids
        assert item.phase_id in phase_ids
        assert item.plan_id == rows["plan"].id
        assert item.status == "pending"


def test_a_generated_retrieval_row_records_when_it_was_approved():
    raw = raw_response()
    raw["items"].append(
        _item(
            "R1",
            1,
            9,
            type="retrieve",
            priority="recurring",
            origin="generated",
            source_item_key="L1",
            estimate_minutes=30,
        )
    )
    result = run(raw, resolutions={"retrieval_approved": ["R1"]})
    rows = build_plan_rows(result.preview, start_date=START)

    generated = next(i for i in rows["items"] if i.origin == "generated")
    assert generated.approved_at is not None
    source = next(i for i in rows["items"] if i.full_title == "Item L1")
    assert generated.source_item_id == source.id


# --- subject tokens ---------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "system-design",
        "senior-backend-interview-prep",
        "distributed-systems",
        "software-architecture",
        "site-reliability-engineering",
        "cloud-infrastructure",
    ],
)
def test_technical_slugs_are_matched_by_token_not_by_exact_string(slug):
    from app.services.study_plan import subject_supports_cards

    assert subject_supports_cards(slug, True) is True


@pytest.mark.parametrize(
    "slug",
    [
        "anatomy",
        "human-anatomy-foundations",
        "constitutional-law",
        "spanish-language",
        "pharmacology-review",
        # The deny list wins even when a technical word is present.
        "anatomy-of-distributed-systems",
        "legal-systems-engineering",
        # Nothing technical in it at all.
        "watercolour-painting",
    ],
)
def test_non_technical_slugs_never_open_the_card_path(slug):
    from app.services.study_plan import subject_supports_cards

    assert subject_supports_cards(slug, True) is False
