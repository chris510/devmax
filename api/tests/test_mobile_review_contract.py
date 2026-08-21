import json
from pathlib import Path

from app.schemas import DueCard, SessionStart

CONTRACT_ROOT = Path(__file__).parents[2] / "contracts" / "mobile" / "review" / "v1"


def _json(name: str):
    return json.loads((CONTRACT_ROOT / name).read_text())


def _validate_exact(model, payload: dict):
    assert set(payload) == set(model.model_fields)
    return model.model_validate(payload)


def test_mobile_review_fixtures_match_backend_models_and_resume_semantics():
    new_card_payload = _json("cards_due.raft.json")[0]
    resumed_card_payload = _json("cards_due.raft.resumed.json")[0]
    new_session_payload = _json("session_start.raft.new.json")
    resumed_session_payload = _json("session_start.raft.resumed.json")

    new_card = _validate_exact(DueCard, new_card_payload)
    resumed_card = _validate_exact(DueCard, resumed_card_payload)
    new_session = _validate_exact(SessionStart, new_session_payload)
    resumed_session = _validate_exact(
        SessionStart,
        resumed_session_payload,
    )

    assert {k: v for k, v in new_card_payload.items() if k != "resumable"} == {
        k: v for k, v in resumed_card_payload.items() if k != "resumable"
    }
    assert {
        k: v for k, v in new_session_payload.items() if k not in {"draft_text", "resumed"}
    } == {
        k: v
        for k, v in resumed_session_payload.items()
        if k not in {"draft_text", "resumed"}
    }

    assert new_card.id == resumed_card.id
    assert not new_card.resumable
    assert not new_session.resumed
    assert new_session.draft_text == ""

    assert resumed_card.resumable
    assert resumed_session.resumed
    assert resumed_session.draft_text
    assert new_session.session_id == resumed_session.session_id
    assert new_session.question == resumed_session.question
