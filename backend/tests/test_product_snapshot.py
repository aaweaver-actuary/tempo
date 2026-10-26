"""Supplied owner captures only; no live capture, SQL or provider process."""
from copy import deepcopy
import json
import sys
import os
from pathlib import Path
import subprocess

import chess
import pytest

from app.repertoire import CapturedSnapshotAdapter, build_index, project_capture
from tempo_game_review_contracts import CanonicalGame
from tempo_game_evidence_contracts.legacy import authority_graph_digest, authority_source_digest, fingerprint


def seal(source):
    source["export_digest"] = fingerprint({key: value for key, value in source.items() if key != "export_digest"})
    return source


def capture():
    moves = ["e2e4", "e7e5", "g1f3"]
    tables = dict(repertoires=[dict(id="rep", scope_source_revision=7, canonical_prefix_moves_json="[]")],
        repertoire_lines=[dict(id="route", repertoire_id="rep", trained_color="white",
                              start_fen=chess.STARTING_FEN, moves_json=json.dumps(moves))],
        cards=[dict(id="physical", revision=4, trained_color="white", content_type="opening",
                    start_fen=chess.STARTING_FEN, moves_json=json.dumps(moves),
                    archived=0, pending_validation=0, superseded_by=None, introduced_at="2026-10-20T00:00:00Z")],
        repertoire_cards=[dict(repertoire_id="rep", card_id="physical")])
    graphs = [dict(repertoire_id="rep", generation=3, line_id="route", card_id="physical",
                   starting_fen=chess.STARTING_FEN, trained_color="white", moves_json=json.dumps(moves))]
    authority = dict(repertoire_id="rep", workspace_id="fixture", scope_source_revision=7,
                     graph_generation=3, graph_state="ready", source_digest=authority_source_digest(tables),
                     graph_digest=authority_graph_digest(graphs))
    return seal(dict(kind="legacy_game_capture", schema_version=1, tables=tables, graphs=graphs,
                     authority=authority, observed_after=deepcopy(authority),
                     captured_at="2026-10-26T12:00:00Z", game=dict(id="game", provider="lichess", provider_game_id="source-game", color="white", content_hash="b"*64,
                         start_fen=chess.STARTING_FEN, moves_json=json.dumps(moves),
                         played_at="2026-10-25T12:00:00Z"), catalogue_complete=True))


def reseal(source):
    source["authority"]["source_digest"] = authority_source_digest(source["tables"])
    source["authority"]["graph_digest"] = authority_graph_digest(source["graphs"])
    source["observed_after"] = deepcopy(source["authority"])
    return seal(source)


def test_snapshot_revision_is_consistent_and_read_only():
    source = capture(); before = deepcopy(source)
    calls = []
    def owner(repertoire_id):
        calls.append(repertoire_id)
        return source
    result = CapturedSnapshotAdapter(owner).snapshot("rep")
    assert calls == ["rep"] and source == before
    assert result.membership.revision == 3
    assert result.membership.source_revision == fingerprint(source["authority"])
    assert result.decision_presentations[0].presentation_id == "physical"
    assert result.decision_presentations[0].presentation_revision == 4
    assert result.decision_presentations[0].introduced_at == "2026-10-20T00:00:00Z"
    assert len(result.decision_presentations) == 2
    assert build_index(result).complete
    source["tables"]["repertoire_lines"][0]["moves_json"] = "[]"
    assert result.membership.routes[0].moves == ("e2e4", "e7e5", "g1f3")
    changed = capture(); changed["observed_after"]["scope_source_revision"] += 1
    with pytest.raises(ValueError, match="snapshot_revision_changed"): project_capture(seal(changed))


def test_unmapped_card_is_not_inferred_from_fen_or_present_existence():
    source = capture(); source["graphs"] = []
    result = project_capture(reseal(source))
    assert result.decision_presentations == ()
    assert build_index(result).routes["rep", "route"][0].binding is None


@pytest.mark.parametrize("value,expected", [(None, "unintroduced"), ("2026-10-20", "unknown"), ("bad", "unknown")])
def test_introduction_is_supplied_evidence_not_card_existence(value, expected):
    source = capture(); source["tables"]["cards"][0]["introduced_at"] = value
    result = project_capture(reseal(source))
    assert result.decision_presentations[0].introduction == expected
    assert result.decision_presentations[0].introduced_at is None


def test_missing_introduction_evidence_stays_unknown():
    source = capture(); del source["tables"]["cards"][0]["introduced_at"]
    assert project_capture(reseal(source)).decision_presentations[0].introduction == "unknown"


def test_ambiguous_physical_mapping_not_guessed():
    source = capture()
    other = deepcopy(source["tables"]["cards"][0]); other["id"] = "other"
    source["tables"]["cards"].append(other)
    source["tables"]["repertoire_cards"].append(dict(repertoire_id="rep", card_id="other"))
    graph = deepcopy(source["graphs"][0]); graph["card_id"] = "other"
    source["graphs"].append(graph)
    index = build_index(project_capture(reseal(source)))
    assert index.routes["rep", "route"][0].binding is None
    assert index.reentry_candidate("rep", chess.STARTING_FEN) is None
    assert {item.code for item in index.diagnostics} == {"ambiguous_physical_mapping"}


def test_capture_digest_bounds_and_requested_scope_are_enforced():
    source = capture(); source["tables"]["cards"][0]["revision"] = 9
    with pytest.raises(ValueError, match="invalid_owner_capture"): project_capture(source)
    with pytest.raises(ValueError, match="snapshot_scope_mismatch"):
        CapturedSnapshotAdapter(lambda _: capture()).snapshot("wrong")
    source = capture(); source["graphs"] *= 257
    with pytest.raises(ValueError, match="exceeds_bound"): project_capture(seal(source))


def test_inconsistent_source_revision_and_unsupported_prefix_are_explicit():
    source = capture(); source["tables"]["repertoires"][0]["scope_source_revision"] = 9
    with pytest.raises(ValueError, match="snapshot_source_revision_mismatch"): project_capture(reseal(source))
    source = capture(); source["tables"]["repertoires"][0]["canonical_prefix_moves_json"] = '["e2e4"]'
    with pytest.raises(ValueError, match="canonical_prefix_snapshot_unsupported"): project_capture(reseal(source))


def test_policy_and_projection_import_without_optional_runtime():
    script = """
import builtins
import chess, pydantic, tempo_game_review_contracts, tempo_game_evidence_contracts.legacy
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.startswith(("app.main", "app.tasks", "app.services", "app.games", "app.study",
                        "tempo_legacy_database", "psycopg", "sqlite3", "requests", "httpx", "chess.engine")):
        raise AssertionError("optional runtime import: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
from app.repertoire import build_index, project_capture
"""
    result = subprocess.run([sys.executable, "-c", script], env=os.environ.copy(),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_existing_donor_physical_card_keeps_interior_decision_identity():
    root = Path(__file__).resolve().parents[2]
    donor = json.loads((root / "services/game-evidence/fixtures/legacy-authority-input.json").read_text())
    source = capture()
    source["tables"], source["graphs"] = donor["tables"], donor["graphs"]
    source["authority"].update(repertoire_id="ruy", graph_generation=7, scope_source_revision=2)
    source["game"] = dict(color="black")
    for row in source["tables"]["repertoires"]:
        row.update(scope_source_revision=2, canonical_prefix_moves_json="[]")
    source["tables"]["cards"][0].update(content_type="opening", superseded_by=None)
    result = project_capture(reseal(source))
    index = build_index(result)
    assert index.routes["ruy", "ruy-route"][5].binding.presentation_id == "physical-black"
    assert index.routes["ruy", "ruy-route"][5].binding.decision_id.startswith("legacy-decision:")
    assert index.routes["italian", "italian-route"][5].binding is None


def test_unbound_transposed_route_does_not_inherit_physical_mapping():
    source = capture()
    duplicate = deepcopy(source["tables"]["repertoire_lines"][0]); duplicate["id"] = "unbound"
    source["tables"]["repertoire_lines"].append(duplicate)
    index = build_index(project_capture(reseal(source)))
    assert index.routes["rep", "unbound"][0].binding is None
    assert any(item.route_id == "unbound" and item.code == "physical_mapping_unknown"
               for item in index.mapping_diagnostics)
    proven = index.routes["rep", "route"][0].binding
    assert proven.presentation_id == "physical"
    assert (proven.route_id, proven.route_ply) == ("route", 0)


def test_missing_current_membership_or_availability_stays_unknown():
    source = capture(); source["tables"]["repertoire_cards"] = []
    assert not project_capture(reseal(source)).decision_presentations
    source = capture(); del source["tables"]["cards"][0]["archived"]
    assert not project_capture(reseal(source)).decision_presentations


def test_current_content_introduction_is_not_assumed_after_revision_change():
    source = capture()
    result = project_capture(source)
    assert result.decision_presentations[0].available
    assert not result.decision_presentations[0].introduction_matches_current_content
    source["tables"]["cards"][0]["revision"] = 1
    assert project_capture(reseal(source)).decision_presentations[0].introduction_matches_current_content
    source["tables"]["cards"][0]["introduction_matches_current_content"] = False
    assert not project_capture(reseal(source)).decision_presentations[0].introduction_matches_current_content


def test_scoped_capture_retains_exclusive_policy_anchor_before_physical_mapping():
    from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken, authority_source_projection
    from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof, LegacyRowDigest
    source = capture()
    manifests = []
    projection = authority_source_projection(source["tables"])
    for name in ("repertoires", "repertoire_lines", "graphs", "cards", "repertoire_cards"):
        digest = LegacyRowDigest()
        rows = ([{"source_row_digest": authority_graph_digest([graph])} for graph in source["graphs"]]
                if name == "graphs" else projection[name])
        for row in rows: digest.add(row)
        manifests.append(dict(name=name, expected_rows=len(rows), visited_rows=len(rows),
            highwater=["end"] if rows else [], last_key=["end"] if rows else [],
            digest=digest.hexdigest(), exhausted=True))
    proof = LegacyScopedCatalogueProof.model_validate(dict(schema_version=1,
        digest_algorithm="legacy-row-stream-v2", authority_id="legacy-graph:" + "0"*64,
        catalogue_generation=2, integrity_certificate=dict(integrity_status="clean",
            scan_status="idle", scan_generation="scan:1", checked_at="2026-10-26T12:00:00Z",
            scan_completed_sources=1, scan_total_sources=1, scan_error=None,
            graph_task_generation=3, graph_task_state="complete", scan_task_id="scan",
            scan_task_generation=1, scan_task_state="complete"),
        scope=dict(game_id="game", content_hash="b"*64, occurred_at="2026-10-25T12:00:00Z",
            learner_color="white", starting_position_key=chess.Board().epd(),
            moves=["e2e4", "e7e5", "g1f3"], decision_plies=[0, 2]),
        streams=manifests, candidate_route_ids=["route"],
        prefix_policies=[["other", "rep"], ["rep"], ["rep"]]))
    token = LegacyAuthorityToken(**(source["authority"] | proof.source_identity_digests()
        | dict(kind="legacy_graph", target_id="synthetic", canonical_prefix_revision=0,
               catalogue_generation=2)))
    proof = proof.model_copy(update={"authority_id": token.storage_identity()[0]})
    source["catalogue_scope"] = proof.model_dump(mode="json")
    source["authority"] = token.model_dump(mode="json")
    source["observed_after"] = deepcopy(source["authority"])
    index = build_index(project_capture(seal(source)))
    assert index.routes["rep", "route"][0].binding is None
    assert index.routes["rep", "route"][2].binding.presentation_id == "physical"
    source["game"]["id"] = "other-game"
    with pytest.raises(ValueError, match="snapshot_game_scope_mismatch"):
        project_capture(seal(source))
    source["game"]["id"] = "game"
    source["catalogue_scope"]["scope"]["occurred_at"] = "2026-10-24T12:00:00Z"
    with pytest.raises(ValueError, match="snapshot_game_scope_mismatch"):
        project_capture(seal(source))
    source["catalogue_scope"]["scope"]["occurred_at"] = "2026-10-25T12:00:00Z"
    source["catalogue_scope"]["integrity_certificate"]["graph_task_generation"] += 1
    with pytest.raises(ValueError, match="snapshot_scope_revision_mismatch"):
        project_capture(seal(source))


def test_unambiguous_original_card_has_complete_first_milestone_mapping():
    source = capture(); source["tables"]["cards"][0]["revision"] = 1
    index = build_index(project_capture(reseal(source)))
    mapping = index.routes["rep", "route"][0].binding
    assert index.complete and not index.mapping_diagnostics
    assert index.lookup("rep", chess.STARTING_FEN).selected_response == "e2e4"
    assert mapping.presentation_id == "physical" and mapping.presentation_revision == 1
    assert mapping.available and mapping.introduction_matches_current_content
    assert mapping.introduction == "introduced" and mapping.introduced_at == "2026-10-20T00:00:00Z"


def comparison_game(source, **changes):
    raw = source["game"]
    return CanonicalGame(**(dict(workspace_id="fixture", game_id=raw["id"], game_revision=1,
        source_id=raw["provider"], source_revision=2, source_digest="c"*64,
        starting_fen=raw["start_fen"], moves=json.loads(raw["moves_json"]),
        learner_color=raw["color"], played_at=raw["played_at"]) | changes))


@pytest.mark.parametrize("before", [True, False])
def test_game_scoped_day_precision_evidence_preserved_without_inventing_timestamp(before):
    source = capture()
    source["tables"]["cards"][0].update(revision=1, introduced_at="2026-10-20", introduced_before_game=before)
    source = reseal(source); game = comparison_game(source)
    snapshot = CapturedSnapshotAdapter(lambda _: source, comparison_game=game).snapshot("rep")
    mapping = build_index(snapshot).routes["rep", "route"][0].binding
    assert mapping.introduced_at is None
    assert mapping.introduced_before_game is before
    assert mapping.introduction_game.provider == game.source_id
    assert mapping.introduction_game.game_id == game.game_id
    assert mapping.introduction_game.source_revision == game.source_revision
    assert mapping.introduction_game.source_digest == game.source_digest
    if before: assert mapping.introduction == "introduced"
    # Without the exact caller-supplied game source, the boolean stays unknown.
    unscoped = project_capture(source).decision_presentations[0]
    assert unscoped.introduced_before_game is None and unscoped.introduction_game is None


@pytest.mark.parametrize("changes", [dict(game_id="different"), dict(source_id="chesscom"),
    dict(learner_color="black"), dict(moves=["d2d4"]), dict(played_at="2026-10-26T12:00:00Z"),
    dict(starting_fen=chess.STARTING_FEN.replace("KQkq", "-"))])
def test_chronology_is_not_bound_to_a_different_captured_game(changes):
    source = capture()
    with pytest.raises(ValueError, match="snapshot_comparison_game_mismatch"):
        project_capture(source, comparison_game=comparison_game(source, **changes))


def test_absent_or_nonboolean_study_chronology_stays_unknown():
    source = capture()
    source["tables"]["cards"][0]["introduced_before_game"] = "true"
    source = reseal(source)
    result = project_capture(source, comparison_game=comparison_game(source))
    assert result.decision_presentations[0].introduced_before_game is None
    assert result.decision_presentations[0].introduction_game is None
