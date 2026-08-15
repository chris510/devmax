from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from app.services import second_brain
from app.services.second_brain import (
    LEARNING_WRITEBACK_SCHEMA,
    LearningNoteError,
    RenderedLearningNote,
    VaultConflictError,
    VaultWriteError,
    build_learning_writeback_bundle,
    canonical_json_bytes,
    confidence_for_score,
    render_learning_note,
    render_learning_notes,
    validate_learning_writeback_bundle,
    write_learning_note,
    write_learning_notes,
)

SOURCE_ID = "00000000-0000-0000-0000-000000000901"
LINEAGE_ID = "00000000-0000-0000-0000-000000000902"
PROPOSAL_ID = "00000000-0000-0000-0000-000000000903"
CARD_ID = "00000000-0000-0000-0000-000000000904"
SESSION_ID = "00000000-0000-0000-0000-000000000905"


def artifact() -> dict[str, object]:
    return {
        "concept": "Consistent Hashing & Virtual Nodes",
        "source_url": "https://example.com/lessons/hash?week=1&mode=read",
        "source_title": 'Hello Interview: "Consistent Hashing"',
        "mental_model": (
            "Keys and nodes share a ring, so membership changes disturb only nearby ranges."
        ),
        "how_it_works": (
            "Hash both keys and virtual-node positions. A key walks clockwise to its first owner; "
            "replicas continue to later distinct physical nodes."
        ),
        "gotchas": [
            "Too few virtual nodes can leave uneven ownership.",
            "A hot key remains hot even when the ring is balanced.",
        ],
        "recall_prompts": {
            "definition_recognition": "What problem does consistent hashing solve?",
            "mechanism": "How does a key find its owner on the ring?",
            "derivation": "Why does adding one node move only a bounded set of keys?",
            "application": "How would you place replicas across physical nodes?",
            "failure_tradeoff": "When do virtual nodes fail to prevent a hot partition?",
        },
        "quiz_results": [
            {
                "date": "2026-08-14",
                "question": "What moves when node six joins?",
                "graded_summary": (
                    "Only keys in the new node's predecessor ranges | not every key."
                ),
                "score": 4,
                "feedback": "Correct mechanism; missed replica placement.",
            }
        ],
        "score": 4,
        "reviewed_on": "2026-08-14",
    }


def writeback_bundle(*, recall_score: object = 4) -> dict[str, object]:
    concept = artifact()
    concept.update(
        {
            "proposal_id": PROPOSAL_ID,
            "card_id": CARD_ID,
            "canonical_question": "How does a key find its owner?",
            "answer_rubric": {
                "mechanism": "Walk clockwise to the first owning virtual node.",
                "acceptable_alternative": "Equivalent ring traversal terminology.",
                "trade_off": "More virtual nodes require more metadata.",
                "failure_mode": "Too few virtual nodes produce imbalance.",
                "misconception": "Membership changes move some keys, not none.",
            },
            "confidence": "established",
        }
    )
    rows = concept["quiz_results"]
    assert isinstance(rows, list)
    rows[0].update(
        {
            "session_id": SESSION_ID,
            "scoring_contract_version": 2,
            "scored_follow_up_used": True,
            "reviewed_at": "2026-08-14T22:42:00Z",
            "recall_score": recall_score,
        }
    )
    return build_learning_writeback_bundle(
        source_id=SOURCE_ID,
        source_lineage_id=LINEAGE_ID,
        source_version=3,
        source_title=concept["source_title"],
        source_url=concept["source_url"],
        source_distilled_at="2026-08-14T22:45:00Z",
        concepts=[concept],
    )


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "second-brain"
    (path / "wiki").mkdir(parents=True)
    (path / "CLAUDE.md").write_text(
        "# Second Brain\n\nLearning notes use `type: learning` and live in `wiki/`.\n",
        encoding="utf-8",
    )
    (path / "wiki" / "_index.md").write_text("# wiki/ — map\n", encoding="utf-8")
    (path / "log.md").write_text("# Operations\n", encoding="utf-8")
    git(path, "init", "-b", "main")
    git(path, "add", "CLAUDE.md", "wiki/_index.md", "log.md")
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_renderer_has_exact_frontmatter_and_escapes_table_content() -> None:
    rendered = render_learning_note(artifact())

    assert rendered.filename == "consistent-hashing-virtual-nodes.md"
    assert rendered.markdown.startswith(
        "---\n"
        "type: learning\n"
        'source: "https://example.com/lessons/hash?week=1&mode=read"\n'
        "created: 2026-08-14\n"
        "confidence: solid\n"
        "tags: []\n"
        "---\n"
    )
    assert (
        "Only keys in the new node's predecessor ranges \\| not every key.<br>_Score 4/5"
        in rendered.markdown
    )
    assert rendered.markdown.count("- **") == 5
    assert "| Date | Question | Grade / evidence |" in rendered.markdown
    assert "[[interview-prep]]" in rendered.markdown
    assert len(rendered.export_id) == 20
    assert rendered.export_id == render_learning_note(artifact()).export_id


@pytest.mark.parametrize(
    ("score", "confidence"),
    [(0, "shaky"), (2, "shaky"), (3, "solid"), (4, "solid"), (5, "teachable")],
)
def test_confidence_mapping(score: int, confidence: str) -> None:
    assert confidence_for_score(score) == confidence


def test_renderer_rejects_raw_source_fields() -> None:
    payload = artifact()
    payload["source_text"] = "the full copied article"

    with pytest.raises(LearningNoteError, match="raw source fields"):
        render_learning_note(payload)


def test_renderer_rejects_free_form_answer_transcripts() -> None:
    payload = artifact()
    rows = payload["quiz_results"]
    assert isinstance(rows, list)
    rows[0]["answer_text"] = "the full free-form answer"

    with pytest.raises(LearningNoteError, match="raw source fields"):
        render_learning_note(payload)


def test_renderer_accepts_list_prompts_and_common_quiz_aliases() -> None:
    payload = artifact()
    prompts = payload["recall_prompts"]
    assert isinstance(prompts, dict)
    payload["recall_prompts"] = [
        {"level": level, "question": question} for level, question in prompts.items()
    ]
    payload["quiz_results"] = {
        "reviewed_at": "2026-08-14T19:42:00Z",
        "question_text": "What moves when node six joins?",
        "graded_summary": "Correctly identified only the acquired ranges.",
        "recall_score": 5,
    }
    payload["proposal_id"] = "e6bda37a-4eb1-4ead-b5bf-cb2bbf61ec41"
    payload["card_id"] = "07c01321-cf4b-4525-8459-d3102285e0d2"
    payload["confidence"] = "established"
    payload.pop("score")
    payload.pop("reviewed_on")

    rendered = render_learning_note(payload)

    assert "confidence: teachable" in rendered.markdown
    assert "created: 2026-08-14" in rendered.markdown


def test_renderer_requires_an_unaided_quiz_result() -> None:
    payload = artifact()
    rows = payload["quiz_results"]
    assert isinstance(rows, list)
    rows[0]["coached"] = True

    with pytest.raises(LearningNoteError, match="unaided"):
        render_learning_note(payload)


def test_writeback_bundle_has_exact_namespaced_contract_and_stable_ids() -> None:
    bundle = writeback_bundle()

    assert bundle["schema"] == LEARNING_WRITEBACK_SCHEMA
    assert bundle["schema_version"] == 1
    assert bundle["producer"] == "devmax"
    assert bundle["source"] == {
        "id": f"devmax:source:{SOURCE_ID}",
        "lineage_id": f"devmax:source-lineage:{LINEAGE_ID}",
        "version": 3,
        "title": 'Hello Interview: "Consistent Hashing"',
        "url": "https://example.com/lessons/hash?week=1&mode=read",
        "distilled_at": "2026-08-14T22:45:00Z",
    }
    concept = bundle["concepts"][0]
    assert concept["id"] == f"devmax:proposal:{PROPOSAL_ID}"
    assert concept["card_id"] == f"devmax:card:{CARD_ID}"
    assert concept["producer_assessment"] == "established"
    assert len(concept["answer_rubric"]) == 5
    assert [row["type"] for row in concept["recall_candidates"]] == [
        "definition_recognition",
        "mechanism",
        "derivation",
        "application",
        "failure_tradeoff",
    ]
    assert concept["recall_candidates"][1]["id"] == (
        f"devmax:probe:{PROPOSAL_ID}:mechanism"
    )
    evidence = concept["quiz_evidence"][0]
    assert evidence["id"] == f"devmax:session:{SESSION_ID}"
    assert evidence["scoring_contract_version"] == 2
    assert evidence["scored_follow_up_used"] is True
    assert bundle == validate_learning_writeback_bundle(bundle)
    assert bundle["export_id"].startswith("sha256:")
    assert bundle == writeback_bundle()


def test_candidate_id_does_not_depend_on_prompt_wording() -> None:
    original = writeback_bundle()
    candidate = original["concepts"][0]["recall_candidates"][0]
    changed = json.loads(json.dumps(original))
    changed_candidate = changed["concepts"][0]["recall_candidates"][0]
    changed_candidate["prompt"] = "A revised recognition prompt?"

    assert changed_candidate["id"] == candidate["id"]
    with pytest.raises(LearningNoteError, match="canonical JSON"):
        validate_learning_writeback_bundle(changed)


def test_writeback_bundle_excludes_private_and_live_scheduler_fields() -> None:
    encoded = canonical_json_bytes(writeback_bundle()).decode("utf-8")

    for forbidden in (
        "source_text",
        "answer_text",
        "transcript",
        "next_review_at",
        "interval_days",
        "mastery_summary",
        "canonical_question",
    ):
        assert forbidden not in encoded


def test_writeback_bundle_fails_closed_without_a_completed_session_score() -> None:
    with pytest.raises(LearningNoteError, match="recall_score must be an integer"):
        writeback_bundle(recall_score=None)


def test_preview_cli_prints_markdown_without_touching_vault(
    tmp_path: Path, vault: Path
) -> None:
    payload_path = tmp_path / "artifact.json"
    payload_path.write_text(json.dumps(artifact()), encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "export_second_brain.py"

    result = subprocess.run(
        [sys.executable, str(script), str(payload_path)],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("---\ntype: learning")
    assert git(vault, "status", "--porcelain=v1") == ""


def test_preview_cli_defaults_to_all_concepts(tmp_path: Path) -> None:
    second = artifact()
    second["concept"] = "Raft Leader Election"
    payload_path = tmp_path / "lesson-artifacts.json"
    payload_path.write_text(
        json.dumps({"concepts": [artifact(), second]}), encoding="utf-8"
    )
    script = Path(__file__).parents[1] / "scripts" / "export_second_brain.py"

    result = subprocess.run(
        [sys.executable, str(script), str(payload_path)],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "<!-- consistent-hashing-virtual-nodes.md -->" in result.stdout
    assert "<!-- raft-leader-election.md -->" in result.stdout


def test_cli_saves_validated_bundle_without_touching_vault(
    tmp_path: Path, vault: Path
) -> None:
    second = artifact()
    second["concept"] = "Raft Leader Election"
    payload_path = tmp_path / "lesson-artifacts.json"
    output_path = tmp_path / "writeback.json"
    payload_path.write_text(
        json.dumps(
            {
                "concepts": [artifact(), second],
                "writeback_bundle": writeback_bundle(),
            }
        ),
        encoding="utf-8",
    )
    script = Path(__file__).parents[1] / "scripts" / "export_second_brain.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(payload_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Saved validated writeback bundle" in result.stdout
    assert json.loads(output_path.read_text(encoding="utf-8")) == writeback_bundle()
    assert git(vault, "status", "--porcelain=v1") == ""


def test_cli_rejects_deprecated_direct_vault_write(tmp_path: Path, vault: Path) -> None:
    payload_path = tmp_path / "lesson-artifacts.json"
    payload_path.write_text(
        json.dumps({"concepts": [artifact()], "writeback_bundle": writeback_bundle()}),
        encoding="utf-8",
    )
    script = Path(__file__).parents[1] / "scripts" / "export_second_brain.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(payload_path),
            "--write",
            "--vault",
            str(vault),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "direct vault writes are deprecated" in result.stderr
    assert git(vault, "status", "--porcelain=v1") == ""


def test_write_creates_note_and_updates_index_and_log(vault: Path) -> None:
    rendered = render_learning_note(artifact())

    result = write_learning_note(
        rendered,
        vault,
        now=datetime.fromisoformat("2026-08-14T15:42:00-07:00"),
    )

    assert result.note_path.read_text(encoding="utf-8") == rendered.markdown
    assert rendered.index_entry in result.index_path.read_text(encoding="utf-8")
    assert (
        "## [2026-08-14 15:42] ingest | "
        "wiki/consistent-hashing-virtual-nodes.md | Added graded Devmax learning note."
        in result.log_path.read_text(encoding="utf-8")
    )
    changed = git(vault, "status", "--porcelain=v1", "--untracked-files=all")
    assert "wiki/consistent-hashing-virtual-nodes.md" in changed
    assert "wiki/_index.md" in changed
    assert "log.md" in changed


def test_write_refuses_dirty_vault(vault: Path) -> None:
    (vault / "log.md").write_text("# Operations\nlocal edit\n", encoding="utf-8")

    with pytest.raises(VaultConflictError, match="local changes"):
        write_learning_note(artifact(), vault)

    assert not (vault / "wiki" / "consistent-hashing-virtual-nodes.md").exists()


def test_write_refuses_existing_note(vault: Path) -> None:
    note = vault / "wiki" / "consistent-hashing-virtual-nodes.md"
    note.write_text("user-authored knowledge\n", encoding="utf-8")
    git(vault, "add", "wiki/consistent-hashing-virtual-nodes.md")
    subprocess.run(
        [
            "git",
            "-C",
            str(vault),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "existing note",
        ],
        check=True,
        capture_output=True,
    )

    with pytest.raises(VaultConflictError, match="already exists"):
        write_learning_note(artifact(), vault)

    assert note.read_text(encoding="utf-8") == "user-authored knowledge\n"


def test_batch_checks_every_conflict_before_writing(vault: Path) -> None:
    second = artifact()
    second["concept"] = "Raft Leader Election"
    existing = vault / "wiki" / "raft-leader-election.md"
    existing.write_text("existing\n", encoding="utf-8")
    git(vault, "add", "wiki/raft-leader-election.md")
    subprocess.run(
        [
            "git",
            "-C",
            str(vault),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "existing note",
        ],
        check=True,
        capture_output=True,
    )
    before_index = (vault / "wiki" / "_index.md").read_bytes()
    before_log = (vault / "log.md").read_bytes()

    with pytest.raises(VaultConflictError, match="already exists"):
        write_learning_notes((artifact(), second), vault)

    assert not (vault / "wiki" / "consistent-hashing-virtual-nodes.md").exists()
    assert (vault / "wiki" / "_index.md").read_bytes() == before_index
    assert (vault / "log.md").read_bytes() == before_log


def test_batch_write_uses_one_index_and_log_update(vault: Path) -> None:
    second = artifact()
    second["concept"] = "Raft Leader Election"
    rendered = render_learning_notes((artifact(), second))

    result = write_learning_notes(
        rendered,
        vault,
        now=datetime.fromisoformat("2026-08-14T15:42:00-07:00"),
    )

    assert {path.name for path in result.note_paths} == {
        "consistent-hashing-virtual-nodes.md",
        "raft-leader-election.md",
    }
    index = result.index_path.read_text(encoding="utf-8")
    assert index.count("graded learning note") == 2
    log = result.log_path.read_text(encoding="utf-8")
    assert log.count("## [2026-08-14 15:42] ingest") == 1
    assert "Added 2 graded Devmax learning notes." in log


def test_write_refuses_non_main_branch(vault: Path) -> None:
    git(vault, "switch", "-c", "draft")

    with pytest.raises(VaultConflictError, match="must be on main"):
        write_learning_note(artifact(), vault)


def test_write_rejects_traversal_in_prebuilt_render(vault: Path) -> None:
    rendered = render_learning_note(artifact())
    unsafe: RenderedLearningNote = replace(rendered, filename="../escape.md")

    with pytest.raises(LearningNoteError, match="basename"):
        write_learning_note(unsafe, vault)

    assert not (vault.parent / "escape.md").exists()


def test_write_refuses_symlinked_wiki_directory(tmp_path: Path, vault: Path) -> None:
    wiki = vault / "wiki"
    outside = tmp_path / "outside-wiki"
    wiki.rename(outside)
    wiki.symlink_to(outside, target_is_directory=True)

    with pytest.raises(VaultWriteError, match="wiki directory"):
        write_learning_note(artifact(), vault)

    assert not (outside / "consistent-hashing-virtual-nodes.md").exists()


def test_atomic_write_rolls_back_every_file(
    monkeypatch: pytest.MonkeyPatch, vault: Path
) -> None:
    before_index = (vault / "wiki" / "_index.md").read_bytes()
    before_log = (vault / "log.md").read_bytes()
    calls = 0
    real_replace = second_brain._replace

    def fail_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        real_replace(source, destination)

    monkeypatch.setattr(second_brain, "_replace", fail_second)

    second = artifact()
    second["concept"] = "Raft Leader Election"

    with pytest.raises(VaultWriteError, match="rolled back"):
        write_learning_notes((artifact(), second), vault)

    assert not (vault / "wiki" / "consistent-hashing-virtual-nodes.md").exists()
    assert not (vault / "wiki" / "raft-leader-election.md").exists()
    assert (vault / "wiki" / "_index.md").read_bytes() == before_index
    assert (vault / "log.md").read_bytes() == before_log
    assert git(vault, "status", "--porcelain=v1", "--untracked-files=all") == ""
