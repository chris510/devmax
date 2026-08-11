"""Versioned score semantics shared by every compatibility response.

V1 remains active. These projections make legacy meaning explicit before any
consumer switches to V2, so routers and clients never infer semantics from a
coincidentally equal integer.
"""

from dataclasses import dataclass
from typing import Literal

from app.models import Card, Session

SCORING_CONTRACT_V1 = 1
SCORING_CONTRACT_V2 = 2

# Stage 1 is compatibility-only. This stays a constant until the V2 scorer and
# rollback path exist; making it an environment switch now would expose a product
# mode the backend cannot honor.
ACTIVE_SCORING_CONTRACT_VERSION: Literal[1] = SCORING_CONTRACT_V1

ScoreKind = Literal["recall", "legacy_composite", "unrated"]
ScoringContractVersion = Literal[1, 2]


@dataclass(frozen=True)
class CardScoreProjection:
    recall_score: int | None
    score_kind: ScoreKind
    scoring_contract_version: ScoringContractVersion | None


@dataclass(frozen=True)
class SessionScoreProjection:
    recall_score: int | None
    legacy_composite_score: int | None
    scoring_contract_version: ScoringContractVersion


def project_card_score(card: Card) -> CardScoreProjection:
    """Expose Recall without relabeling a composite-only legacy value."""
    if card.last_accuracy is not None:
        return CardScoreProjection(
            recall_score=card.last_accuracy,
            score_kind="recall",
            scoring_contract_version=card.last_score_contract_version or SCORING_CONTRACT_V1,
        )
    if card.last_score is not None:
        return CardScoreProjection(
            recall_score=None,
            score_kind="legacy_composite",
            scoring_contract_version=card.last_score_contract_version
            or SCORING_CONTRACT_V1,
        )
    return CardScoreProjection(
        recall_score=None,
        score_kind="unrated",
        scoring_contract_version=None,
    )


def project_session_score(session: Session) -> SessionScoreProjection:
    """Keep V1 composite history separate from its Accuracy/Recall signal."""
    version = session.scoring_contract_version
    return SessionScoreProjection(
        recall_score=session.accuracy,
        legacy_composite_score=session.score if version == SCORING_CONTRACT_V1 else None,
        scoring_contract_version=version,
    )
