"""Repository-level guardrails for the iOS/server consent release contract."""

import re
from pathlib import Path

from app.consent_policy import LATEST_POLICY_VERSION, policy_for

REPO_ROOT = Path(__file__).resolve().parents[2]


def _one_match(pattern: str, path: Path) -> str:
    matches = re.findall(pattern, path.read_text())
    assert len(matches) == 1, f"expected one consent-contract value in {path}: {matches}"
    return matches[0]


def test_ios_and_server_latest_consent_policy_versions_cannot_drift() -> None:
    ios_version = _one_match(
        r'static let policyVersion = "([^"]+)"',
        REPO_ROOT / "ios/Devmax/Models/PublicModels.swift",
    )

    assert ios_version == LATEST_POLICY_VERSION


def test_latest_policy_requires_a_new_enough_ios_build() -> None:
    build = int(
        _one_match(
            r'CURRENT_PROJECT_VERSION: "(\d+)"',
            REPO_ROOT / "ios/project.yml",
        )
    )

    assert build >= policy_for(LATEST_POLICY_VERSION).minimum_ios_build
