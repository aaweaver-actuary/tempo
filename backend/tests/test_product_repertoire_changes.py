"""Task-owned legal fixtures exercise the Repertoire approval boundary."""
import json
from pathlib import Path
import sys

import chess
import pytest

ROOT = Path(__file__).resolve().parents[2]
for source in ("packages/game-review-contracts/src", "packages/chess-core/src", "packages/evidence-math/src"):
    sys.path.insert(0, str(ROOT / source))
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire.changes.application import conflict_report, study_eligibility, require_valid_change
from app.repertoire.changes.ports import ChangeError


def snapshot(*routes):
    values = []
    for identifier, moves, repertoire in routes:
        values.append(dict(route_id=identifier, repertoire_id=repertoire, policy_id=repertoire,
            learner_color="white", starting_fen=chess.STARTING_FEN, moves=moves, reentries=[]))
    return RepertoireSnapshot.model_validate_json(json.dumps(dict(
        membership=dict(workspace_id="task492", snapshot_id="fixture", revision=1,
            digest="a"*64, source_revision="source-1", captured_at="2026-10-26T12:00:00Z",
            scope_id="task492", scope_repertoire_ids=sorted({item[2] for item in routes}),
            complete=True, available=True, routes=values), variant="standard", learner_color="white",
        boundaries=[dict(route_id=item[0], terminal_ply=len(item[1]), deliberate=True) for item in routes],
        decision_presentations=[])))


def eligibility(value, route, repertoire="rep", ply=0, **changes):
    return study_eligibility(value, repertoire_id=repertoire, route_id=route, ply=ply,
        **(dict(source_revision="source-1", revision=1, digest="a"*64) | changes))


def test_same_repertoire_conflict_blocks_only_affected_occurrences():
    value = snapshot(("a", ["e2e4"], "rep"), ("b", ["d2d4"], "rep"),
        ("c", ["d2d4"], "other"))
    report = conflict_report(value)
    assert len(report.conflicts) == 1 and not report.clean
    assert report.conflicts[0].responses_uci == ("d2d4", "e2e4")
    assert {item.route_id for item in report.conflicts[0].occurrences} == {"a", "b"}
    denied = eligibility(value, "a")
    assert not denied.eligible and denied.suspend_without_lapse
    assert denied.reason == "own_response_conflict"
    assert eligibility(value, "c", "other").eligible


def test_opponent_fork_and_different_repertoire_choices_remain_valid():
    value = snapshot(("a", ["e2e4", "e7e5"], "rep"),
        ("b", ["e2e4", "c7c5"], "rep"), ("c", ["d2d4"], "other"))
    assert conflict_report(value).clean
    require_valid_change(value)


def test_transposed_own_conflict_retains_all_occurrences():
    value = snapshot(("a", ["g1f3", "d7d5", "g2g3", "c7c5", "f1g2"], "rep"),
        ("b", ["g2g3", "c7c5", "g1f3", "d7d5", "d2d4"], "rep"))
    report = conflict_report(value)
    assert any({(item.route_id, item.ply) for item in conflict.occurrences}
               == {("a", 4), ("b", 4)} for conflict in report.conflicts)


def test_invalid_tail_retains_diagnostic_and_never_certifies_prefix():
    value = snapshot(("bad", ["e2e4", "e7e5", "e2e3"], "rep"),
        ("good", ["d2d4"], "other"))
    report = conflict_report(value)
    assert not report.clean and any(item.code == "illegal_move" for item in report.diagnostics)
    assert eligibility(value, "bad").reason == "invalid_source"
    assert eligibility(value, "good", "other").eligible
    with pytest.raises(ChangeError, match="invalid_source"): require_valid_change(value)


def test_stale_or_unpublished_report_cannot_reopen_target():
    value = snapshot(("a", ["e2e4"], "rep"))
    assert eligibility(value, "a", revision=2).reason == "stale_integrity"
    raw = value.model_dump(mode="json"); raw["membership"]["available"] = False
    unpublished = RepertoireSnapshot.model_validate_json(json.dumps(raw))
    assert eligibility(unpublished, "a").reason == "unpublished_integrity"
    assert not conflict_report(unpublished).clean
