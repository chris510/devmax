import argparse
import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from app.services.card_lifecycle import Grounding
from scripts import v2_recall_eval
from scripts.effort_sweep_support import hydrate_grounding

API = Path(__file__).resolve().parents[1]
PACK = API / "scripts" / "grounded_recall_v2_cases_stage2_draft.json"
CARDS = API / "cards.json"

AUTHORITY_FIELDS = {
    "question",
    "canonical_question",
    "answer_anchor",
    "answer_basis",
    "answer_rubric",
    "source_excerpt",
    "source_url",
    "source_section",
    "source_label",
    "evidence",
    "grounding_status",
}
RISK_TAGS = {
    "speech-noise",
    "partial-self-correction",
    "adjacent-jargon",
    "source-compatible-alternative",
    "prior-summary-contradiction",
    "follow-up-anchored",
}
REQUIRED_INITIAL = {0, 1, 3, 4}
REQUIRED_TERMINAL = {1, 2, 3, 4}
READINESS_BLOCKER = (
    "production qualification remains blocked: 12 Week 2-3 cards await "
    "owner approval"
)


def load(path: Path) -> list[dict]:
    value = json.loads(path.read_text())
    assert isinstance(value, list)
    return value


def manifest_by_topic(cards: list[dict] | None = None) -> dict[str, dict]:
    return {card["topic"]: card for card in cards or load(CARDS)}


def grounding(card: dict) -> Grounding:
    return Grounding(
        source_url=card.get("source_url", ""),
        source_section=card.get("source_section", ""),
        source_label=card.get("source_label", ""),
        answer_basis=card.get("answer_basis", ""),
        answer_rubric=card.get("answer_rubric"),
        canonical_question=card.get("canonical_question", ""),
    )


def pending_failures(cases: list[dict]) -> list[str]:
    return [
        f"{case['name']}: review_status must be explicitly 'approved'"
        for case in cases
    ]


def test_stage2_draft_is_unique_pending_and_authority_free() -> None:
    cases = load(PACK)

    assert len(cases) == 38
    assert len({case["name"] for case in cases}) == len(cases)
    assert all(case["review_status"] == "pending" for case in cases)
    assert all("owner human review" in case["review_note"] for case in cases)
    assert all(
        {"v2-recall-draft", "pending-owner-review"} <= set(case["tags"])
        for case in cases
    )
    assert all(not (AUTHORITY_FIELDS & case.keys()) for case in cases)

    # Every proposed score and flow already obeys the deterministic turn policy.
    # The only human-label failure is the deliberately withheld approval.
    assert v2_recall_eval.human_label_failures(cases) == pending_failures(cases)


def test_stage2_draft_covers_18_cards_and_every_week_boundary() -> None:
    cases = load(PACK)
    cards = manifest_by_topic()
    topics = {case["topic"] for case in cases}

    assert len(topics) == 18
    assert topics <= cards.keys()
    selected = [cards[topic] for topic in topics]
    assert Counter(card["target_week"] for card in selected) == {1: 6, 2: 6, 3: 6}

    week_by_topic = {card["topic"]: card["target_week"] for card in selected}
    assert Counter(week_by_topic[case["topic"]] for case in cases) == {
        1: 22,
        2: 8,
        3: 8,
    }

    for week in (1, 2, 3):
        week_cases = [
            case for case in cases if week_by_topic[case["topic"]] == week
        ]
        initial = {
            case["expected_recall"]
            for case in week_cases
            if not case.get("probes")
        }
        terminal = {
            case["expected_recall"]
            for case in week_cases
            if "terminal-turn" in case["tags"]
        }
        assert REQUIRED_INITIAL <= initial
        assert REQUIRED_TERMINAL <= terminal

        initial_policy = {
            (case["expected_recall"], case["expected_flow"])
            for case in week_cases
            if "initial-boundary-0-1" in case["tags"]
            or "initial-boundary-3-4" in case["tags"]
        }
        assert initial_policy == {
            (0, "complete"),
            (1, "follow_up"),
            (3, "follow_up"),
            (4, "complete"),
        }

        for tag, expected_values in {
            "terminal-boundary-1-2": {1, 2},
            "terminal-boundary-2-3": {2, 3},
            "terminal-boundary-3-4": {3, 4},
        }.items():
            boundary = [case for case in week_cases if tag in case["tags"]]
            assert {case["expected_recall"] for case in boundary} == expected_values
            assert all(case["expected_flow"] == "complete" for case in boundary)
            assert all(case.get("probes") for case in boundary)


def test_stage2_draft_covers_every_v2_risk_and_flow_shape() -> None:
    cases = load(PACK)
    cards = manifest_by_topic()
    week_by_topic = {
        card["topic"]: card["target_week"]
        for card in cards.values()
        if card["target_week"] in {1, 2, 3}
    }

    assert {case["expected_recall"] for case in cases} == set(range(6))
    observed_risks = {tag for case in cases for tag in case["tags"]}
    assert RISK_TAGS <= observed_risks

    stale = [
        case for case in cases if "prior-summary-contradiction" in case["tags"]
    ]
    assert stale
    assert all(case.get("mastery_summary") for case in stale)

    insufficiency = [
        case for case in cases if "one-probe-insufficiency" in case["tags"]
    ]
    assert insufficiency
    assert all(len(case["probes"]) == 1 for case in insufficiency)
    assert all(case["expected_flow"] == "follow_up" for case in insufficiency)

    capped = [case for case in cases if "two-probe-cap" in case["tags"]]
    assert {week_by_topic[case["topic"]] for case in capped} == {1, 2, 3}
    assert all(len(case["probes"]) == 2 for case in capped)
    assert all(case["expected_flow"] == "complete" for case in capped)

    follow_up_evidence = [
        case
        for case in cases
        if "follow-up-anchored" in case["tags"]
        and case["expected_flow"] == "complete"
    ]
    assert follow_up_evidence
    assert all(case.get("probes") for case in follow_up_evidence)


def test_draft_grounding_blocks_production_until_owner_approves(
    tmp_path: Path,
) -> None:
    cases = load(PACK)
    cards = load(CARDS)
    drafts = [
        card for card in cards if card["target_week"] in {2, 3}
    ]

    assert len(drafts) == 12, READINESS_BLOCKER
    assert Counter(card["target_week"] for card in drafts) == {2: 6, 3: 6}
    assert all(card.get("grounding_status") == "draft_review" for card in drafts)
    assert all(not grounding(card).missing() for card in drafts)
    assert {card["topic"] for card in drafts} == {
        case["topic"] for case in cases if case.get("target_week") in {2, 3}
    }

    # The real manifest must remain unusable for paid evaluation while those
    # owner approvals are withheld.
    with pytest.raises(SystemExit, match="2"):
        hydrate_grounding(cases, CARDS, argparse.ArgumentParser())

    approved_copy = copy.deepcopy(cards)
    for card in approved_copy:
        if card["target_week"] in {2, 3}:
            card["grounding_status"] = "approved"
    approved_manifest = tmp_path / "cards.json"
    approved_manifest.write_text(json.dumps(approved_copy))

    hydrated = hydrate_grounding(
        cases,
        approved_manifest,
        argparse.ArgumentParser(),
    )
    assert len(hydrated) == 38
    assert len({case["topic"] for case in hydrated}) == 18
    assert all(
        {
            "question",
            "answer_basis",
            "answer_rubric",
            "source_url",
            "source_section",
            "source_label",
            "evidence",
        }
        <= case.keys()
        for case in hydrated
    )

    # With provenance hypothetically approved, the complete Stage-2 gate has
    # no coverage or grounding failure. The 38 explicit human label decisions
    # remain pending, so paid trials are still impossible.
    assert v2_recall_eval.stage2_pack_failures(hydrated) == pending_failures(cases)
