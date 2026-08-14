import argparse
import json
from collections import Counter
from pathlib import Path

from app.services.card_lifecycle import Grounding
from scripts import v2_recall_eval
from scripts.effort_sweep_support import hydrate_grounding

API = Path(__file__).resolve().parents[1]
PACK = API / "scripts" / "grounded_recall_v2_cases_week1_draft.json"
CARDS = API / "cards.json"

AUTHORITY_FIELDS = {
    "question",
    "canonical_question",
    "answer_basis",
    "answer_rubric",
    "source_url",
    "source_section",
    "source_label",
}
RISK_TAGS = {
    "speech-noise",
    "partial-self-correction",
    "adjacent-jargon",
    "source-compatible-alternative",
    "prior-summary-contradiction",
    "follow-up-anchored",
}
READINESS_BLOCKER = (
    "production qualification remains blocked: 12 Week 2-3 cards lack "
    "owner-approved complete grounding"
)


def load(path: Path) -> list[dict]:
    value = json.loads(path.read_text())
    assert isinstance(value, list)
    return value


def manifest_by_topic() -> dict[str, dict]:
    return {card["topic"]: card for card in load(CARDS)}


def grounding(card: dict) -> Grounding:
    return Grounding(
        source_url=card.get("source_url", ""),
        source_section=card.get("source_section", ""),
        source_label=card.get("source_label", ""),
        answer_basis=card.get("answer_basis", ""),
        answer_rubric=card.get("answer_rubric"),
        canonical_question=card.get("canonical_question", ""),
    )


def test_draft_labels_are_explicitly_pending_and_policy_checkable() -> None:
    cases = load(PACK)

    assert len(cases) == 22
    assert len({case["name"] for case in cases}) == len(cases)
    assert all(case["review_status"] == "pending" for case in cases)
    assert all("owner human review" in case["review_note"] for case in cases)
    assert all(
        {"v2-recall-draft", "week1-canary-only", "pending-owner-review"}
        <= set(case["tags"])
        for case in cases
    )
    assert all(not (AUTHORITY_FIELDS & case.keys()) for case in cases)

    # The proposed numeric/flow fields obey the deterministic V2 turn policy,
    # but pending review is deliberately the one validation failure per case.
    failures = v2_recall_eval.human_label_failures(cases)
    assert len(failures) == len(cases)
    assert all(
        failure.endswith("review_status must be explicitly 'approved'")
        for failure in failures
    )


def test_six_week_one_topics_hydrate_from_approved_complete_cards() -> None:
    cases = load(PACK)
    cards = manifest_by_topic()
    topics = {case["topic"] for case in cases}

    assert len(topics) == 6
    assert topics <= cards.keys()
    selected = [cards[topic] for topic in topics]
    assert Counter(card["target_week"] for card in selected) == {1: 6}
    assert all(card.get("grounding_status") == "approved" for card in selected)
    assert all(not grounding(card).missing() for card in selected)

    hydrated = hydrate_grounding(cases, CARDS, argparse.ArgumentParser())
    assert len(hydrated) == len(cases)
    assert all(case["question"] for case in hydrated)
    assert all(case["answer_basis"] for case in hydrated)
    assert all(case["answer_rubric"] for case in hydrated)


def test_draft_covers_every_v2_behavior_boundary_and_risk_shape() -> None:
    cases = load(PACK)

    assert {case["expected_recall"] for case in cases} == set(range(6))

    initial_zero_one = {
        (case["expected_recall"], case["expected_flow"])
        for case in cases
        if "initial-boundary-0-1" in case["tags"]
    }
    initial_three_four = {
        (case["expected_recall"], case["expected_flow"])
        for case in cases
        if "initial-boundary-3-4" in case["tags"]
    }
    assert initial_zero_one == {(0, "complete"), (1, "follow_up")}
    assert initial_three_four == {(3, "follow_up"), (4, "complete")}

    for tag, expected_values in {
        "terminal-boundary-1-2": {1, 2},
        "terminal-boundary-2-3": {2, 3},
        "terminal-boundary-3-4": {3, 4},
    }.items():
        boundary = [case for case in cases if tag in case["tags"]]
        assert {case["expected_recall"] for case in boundary} == expected_values
        assert all(case["expected_flow"] == "complete" for case in boundary)
        assert all("terminal-turn" in case["tags"] for case in boundary)
        assert all(len(case["probes"]) == 2 for case in boundary)

    insufficiency = [
        case for case in cases if "one-probe-insufficiency" in case["tags"]
    ]
    capped = [case for case in cases if "two-probe-cap" in case["tags"]]
    assert len(insufficiency) >= 1
    assert all(len(case["probes"]) == 1 for case in insufficiency)
    assert all(case["expected_flow"] == "follow_up" for case in insufficiency)
    assert len(capped) >= 4
    assert all(len(case["probes"]) == 2 for case in capped)
    assert all(case["expected_flow"] == "complete" for case in capped)

    observed_risks = {tag for case in cases for tag in case["tags"]}
    assert RISK_TAGS <= observed_risks
    stale = [
        case for case in cases if "prior-summary-contradiction" in case["tags"]
    ]
    assert all(case.get("mastery_summary") for case in stale)
    follow_up_evidence = [
        case
        for case in cases
        if "follow-up-anchored" in case["tags"]
        and case["expected_flow"] == "complete"
    ]
    assert follow_up_evidence
    assert all(len(case["probes"]) == 1 for case in follow_up_evidence)


def test_week_one_draft_is_canary_only_and_names_the_production_blocker() -> None:
    cases = load(PACK)
    cards = load(CARDS)
    by_topic = {card["topic"]: card for card in cards}
    topics = {case["topic"] for case in cases}
    weeks = {by_topic[topic]["target_week"] for topic in topics}

    assert len(topics) == 6 < 18, READINESS_BLOCKER
    assert weeks == {1}
    assert len(weeks) < 3, READINESS_BLOCKER
    assert all("week1-canary-only" in case["tags"] for case in cases)

    blockers = [
        card
        for card in cards
        if card["target_week"] in {2, 3}
        and (
            card.get("grounding_status") != "approved"
            or bool(grounding(card).missing())
        )
    ]
    assert len(blockers) == 12, READINESS_BLOCKER
    assert Counter(card["target_week"] for card in blockers) == {2: 6, 3: 6}
    assert {card["topic"] for card in blockers} == {
        "Data modeling from access patterns: deriving entities, relationships, and query shapes",
        "Denormalization: duplicating data for a read path without losing update correctness",
        "B-tree lookup and range scans: how page locality shapes read cost",
        "Composite indexes: left-prefix matching, column order, and covering a query",
        "Write-ahead logging: committing before data pages and recovering after a crash",
        "MVCC snapshots: letting readers and writers proceed while isolation "
        "anomalies remain bounded",
        "Cache-aside reads: miss handling, population, invalidation, and stale-data windows",
        "Cache stampede and hot keys: request coalescing, jitter, replication, "
        "and negative caching",
        "Hash sharding: routing a key, adding capacity, and migrating ownership",
        "Range sharding: preserving scans while detecting and splitting hot ranges",
        "Consistent hashing: ring ownership, virtual nodes, and bounded key movement",
        "CAP during a partition: choosing which operations remain available and which must reject",
    }, READINESS_BLOCKER
