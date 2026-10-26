"""Synthetic source integration; native PostgreSQL/product proof is separate.

The existing physical mapping is supplied by the shared snapshot fixture, never
created from the PGN or a matching position. No live data is used.
"""
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from app.contracts.product_v02.games import GameRecord
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.product_composition.feedback import main, prepare_record_feedback, submit_feedback
from app.study.game_feedback import GameFeedback
from test_product_game_lapse import Decisions, MemoryStore, NOW

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures"
PGN = '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. d4 1-0\n'


@pytest.fixture
def inputs():
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(
        json.loads((FIXTURES / "snapshot.json").read_text())["payload"]))
    record = GameRecord(source_game_id="already-imported-synthetic-game", source_revision=1,
        source_digest=hashlib.sha256(PGN.encode()).hexdigest(), pgn=PGN,
        played_at="2026-10-25T12:00:00Z", time_precision="second")
    return record, snapshot


def prepare(inputs, **changes):
    record, snapshot = inputs
    return prepare_record_feedback(record, provider="chesscom", account="Learner",
        workspace_id=snapshot.membership.workspace_id, snapshot=snapshot,
        detected_at=changes.pop("detected_at", NOW.isoformat()), **changes)


def test_imported_game_maps_to_existing_card(inputs):
    prepared = prepare(inputs)
    mapping = inputs[1].decision_presentations[0]
    assert prepared.lapse.finding.presentation_id == mapping.presentation_id == "existing-physical-card"
    assert prepared.lapse.finding.decision_id == mapping.decision_id
    assert prepared.lapse.finding.played_uci == "d2d4"
    assert prepared.lapse.finding.expected_uci == ("e2e4",)
    assert prepared.lapse.finding.ply == 1
    assert prepared.snapshot_digest == inputs[1].membership.digest


def test_duplicate_pipeline_has_one_lapse(inputs):
    store, decisions = MemoryStore(), Decisions()
    owner = GameFeedback(store, decisions, clock=lambda: NOW)
    first = prepare(inputs)
    receipt = submit_feedback(first, owner)
    retried = prepare(inputs, detected_at=(NOW + timedelta(days=1)).isoformat())
    duplicate = submit_feedback(retried, owner)
    assert first.operation_identity == retried.operation_identity
    assert receipt.status == duplicate.status == "applied"
    assert duplicate.outcome == "duplicate"
    assert receipt.available_by == duplicate.available_by
    assert len(store.reviews) == len(store.effects) == 1
    assert store.history == ["retained-original-review"]


def test_missing_mapping_cannot_prepare_a_lapse(inputs):
    record, snapshot = inputs
    unmapped = snapshot.model_copy(update={"decision_presentations": ()})
    with pytest.raises(ValueError, match="one_confirmed_decision_occurrence_required"):
        prepare((record, unmapped))


def test_preview_is_private_read_only_and_never_overwrites_retry_payload(inputs, tmp_path, capsys):
    record, snapshot = inputs
    record_path, snapshot_path, output = (tmp_path / name for name in ("record.json", "snapshot.json", "preview.json"))
    record_path.write_text(record.model_dump_json())
    snapshot_path.write_text(snapshot.model_dump_json())
    arguments = ["--record", str(record_path), "--snapshot", str(snapshot_path),
        "--provider", "chesscom", "--account", "Learner", "--workspace-id",
        snapshot.membership.workspace_id, "--detected-at", NOW.isoformat(), "--output", str(output)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == dict(prepared=True, delivered=False, read_only=True)
    saved = output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        main(arguments)
    assert output.read_bytes() == saved
