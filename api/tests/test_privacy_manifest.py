from __future__ import annotations

import plistlib
from pathlib import Path


def test_linked_llm_audit_diagnostics_are_declared_account_linked() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[2]
        / "ios"
        / "Devmax"
        / "PrivacyInfo.xcprivacy"
    )
    with manifest_path.open("rb") as manifest_file:
        manifest = plistlib.load(manifest_file)

    collected = {
        item["NSPrivacyCollectedDataType"]: item
        for item in manifest["NSPrivacyCollectedDataTypes"]
    }
    diagnostics = collected["NSPrivacyCollectedDataTypeOtherDiagnosticData"]

    assert diagnostics["NSPrivacyCollectedDataTypeLinked"] is True
    assert diagnostics["NSPrivacyCollectedDataTypeTracking"] is False
    assert diagnostics["NSPrivacyCollectedDataTypePurposes"] == [
        "NSPrivacyCollectedDataTypePurposeAppFunctionality"
    ]
