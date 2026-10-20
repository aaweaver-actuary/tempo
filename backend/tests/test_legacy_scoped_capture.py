"""Synthetic complete traversal, scoped provenance and existing integrity owner."""
from copy import deepcopy
from dataclasses import replace
import json
import re
from types import SimpleNamespace

import pytest
from tempo_game_evidence_contracts import v3
from tempo_game_evidence_contracts.legacy import decision_identity, fingerprint
from tempo_learning_remediation import RemediationError

from app.services import legacy_game_authority as owner
from app.services.legacy_game_capture import capture_scope, DEFAULT_BUDGET, Traversal, Stream
from app.services.postgres_integrity import integrity_certificate_from_row
from test_legacy_game_evidence_commands import (fixture, prepared, semantic_check,
    integrity_row, Writer, fake_repository, commands)


class SourceRows:
    def __init__(self, captured):
        self.captured, self.calls = deepcopy(captured), []
        self.rows = deepcopy(captured.tables)
        self.rows["graphs"] = [{**row, "decision_index": index} for index, row in enumerate(captured.graphs)]
        self.keys = dict(repertoires=("id",), repertoire_lines=("id",), cards=("id",),
                         repertoire_cards=("repertoire_id", "card_id"), graphs=("line_id", "decision_index"))
        self.truncate = None
        self.break_seek = False

    def read(self, statement, parameters=()):
        self.calls.append((statement, parameters))
        match = re.search(r"/\*legacy-stream:(\w+):(\w+)\*/", statement)
        if match:
            name, stage = match.groups()
            key = lambda row: tuple(row[key] for key in self.keys[name])
            rows = sorted(self.rows[name], key=key)
            if stage == "count": return [dict(row_count=len(rows))]
            if stage == "highwater": return [{field: rows[-1][field] for field in self.keys[name]}] if rows else []
            assert "LIMIT 64" in statement and "_seek_advanced" in statement
            size = len(self.keys[name])
            cursor, highwater = tuple(parameters[:size]), tuple(parameters[-size:])
            rows = [row for row in rows if cursor < key(row) <= highwater][:64]
            if self.truncate == name: rows = rows[:1]
            return [{**row, "_seek_advanced": not self.break_seek} for row in rows]
        if "legacy_learning_identity" in statement:
            return [dict(target_id=self.captured.target_id, workspace_id="legacy")]
        if "imported_games" in statement: return [deepcopy(self.captured.game)]
        pytest.fail("Unexpected source read: " + statement)

    def metadata(self, _scope):
        return deepcopy(self.captured.metadata)


def source_fixture(filler=0, competitor=None):
    value, captured = fixture()
    source = SourceRows(captured)
    if filler:
        source.rows["repertoires"].append({**source.rows["repertoires"][0], "id": "filler", "is_main": 0})
    for index in range(filler):
        source.rows["repertoire_lines"].append(dict(id=f"filler-{index:05}", repertoire_id="filler",
            trained_color="white", start_fen=captured.game["start_fen"], moves_json='["d2d4"]'))
    if competitor:
        route = {**source.rows["repertoire_lines"][0], "id": "zz-competitor",
            "moves_json": captured.game["moves_json"]}
        if competitor == "foreign": route["repertoire_id"] = "italian"
        source.rows["repertoire_lines"].append(route)
        if competitor == "unsupported":
            source.rows["cards"].append({**source.rows["cards"][0], "id": "unsupported-physical", "revision": 2,
                "moves_json": route["moves_json"]})
            source.rows["graphs"].append({**source.rows["graphs"][0], "line_id": route["id"],
                "card_id": "unsupported-physical", "decision_index": 1, "moves_json": route["moves_json"]})
            source.rows["repertoire_cards"].append(dict(repertoire_id="ruy", card_id="unsupported-physical"))
    return value, source


@pytest.mark.parametrize('variant', ['noncapturable_ep', 'counters', 'capturable_ep', 'turn', 'castling'])
def test_scoped_physical_capture_compares_legal_positions_and_preserves_source_bytes(variant):
    import chess
    from app.services.legacy_game_capture import capture_source_scope, CaptureRequest
    value, source = source_fixture()
    prefix = ['e2e4','d7d5','e4d5','d8d5','b1c3','d5a5','d2d4']
    continuation = ['c8f5','d1f3']
    if variant == 'capturable_ep':
        prefix, continuation = ['e2e4','a7a6','e4e5','d7d5'], ['e5e6']
    board = chess.Board()
    for move in prefix: board.push_uci(move)
    physical_fen = board.fen(en_passant='legal')
    if variant == 'counters': physical_fen = physical_fen.rsplit(' ',2)[0]+' 17 42'
    if variant == 'capturable_ep': physical_fen = physical_fen.replace(' d6 ', ' - ')
    if variant == 'turn': physical_fen = physical_fen.replace(' b ', ' w ')
    if variant == 'castling': physical_fen = physical_fen.replace(' KQkq ', ' - ')
    route = source.rows['repertoire_lines'][0]
    route.update(trained_color='white', moves_json=json.dumps(prefix+continuation))
    source.rows['repertoire_lines'] = [route]
    card, graph = source.rows['cards'][0], source.rows['graphs'][0]
    card.update(start_fen=physical_fen, trained_color='white', moves_json=json.dumps(continuation))
    graph.update(starting_fen=physical_fen, trained_color='white', moves_json=json.dumps(continuation))
    source.captured.game.update(color='white', moves_json=route['moves_json'])
    before = deepcopy(source.rows)
    scope = CaptureRequest('ruy', value.game_id, (len(prefix)+len(continuation)-1,))
    actual = capture_source_scope(scope, source.read, source.metadata)
    assert source.rows == before
    if variant in {'noncapturable_ep', 'counters'}:
        assert actual.graphs == [graph]
        assert actual.tables['cards'][0]['start_fen'] == physical_fen
        assert actual.tables['cards'][0]['id'] == card['id']
    else:
        assert actual.graphs == [] and actual.tables['cards'] == []


def captured_scope(value, source, **options):
    captured = capture_scope(value, source.read, source.metadata, **options)
    data = value.model_dump(mode="json")
    data["authority"] = owner.current_token(captured, "ruy").model_dump(mode="json")
    return v3.EvidenceDelivery.model_validate(data), captured


def test_global_exhaustion_past_4096_retains_a_small_exact_physical_scope_and_owner_proof():
    value, source = source_fixture(filler=4100)
    value, captured = captured_scope(value, source)
    proof = captured.catalogue_proof
    assert proof.stream("repertoire_lines").visited_rows == 4102
    assert proof.stream("repertoire_lines").exhausted
    assert proof.candidate_route_ids == ["ruy-route"]
    assert proof.prefix_policies[-1] == ["ruy"] and proof.authority_anchor("ruy") == 5
    assert len(captured.tables["cards"]) == 1 and len(captured.tables["repertoire_lines"]) == 1
    contributions = semantic_check(prepared(value, captured))
    assert len(contributions) == 1 and contributions[0].card_id == "physical-black"
    assert json.loads(contributions[0].payload_json)["decision_scope_proof"] == proof.model_dump(mode="json")
    pages = [call for call in source.calls if "legacy-stream:repertoire_lines:page" in call[0]]
    assert len(pages) == 65


@pytest.mark.parametrize("competitor,reason", [("foreign", "route_policy_ambiguous"),
    ("unbound", "unknown_is_not_permission"), ("unsupported", "unknown_is_not_permission")])
def test_competing_route_beyond_4096_is_retained_and_still_vetoes_acceptance(competitor, reason):
    value, source = source_fixture(filler=4100, competitor=competitor)
    value, captured = captured_scope(value, source)
    assert captured.catalogue_proof.candidate_route_ids[-1] == "zz-competitor"
    if competitor == "unsupported":
        assert any(card["id"] == "unsupported-physical" and card["revision"] == 2 for card in captured.tables["cards"])
    with pytest.raises(RemediationError, match=reason): semantic_check(prepared(value, captured))


@pytest.mark.parametrize("ended_ply", [4, 5])
def test_forged_miss_cannot_drop_foreign_policy_ending_at_decision(ended_ply):
    value, source = source_fixture()
    # Italian shares the actual prefix through Bb5, then has no authored reply.
    # It is absent from retained executable candidates, but still prevents an
    # exclusive policy anchor. A producer's claimed miss is no permission.
    source.rows["repertoire_lines"].append({**source.rows["repertoire_lines"][0],
        "id": "ended-italian", "repertoire_id": "italian",
        "moves_json": json.dumps(json.loads(source.captured.game["moves_json"])[:ended_ply])})
    value, captured = captured_scope(value, source)
    if ended_ply < 5:
        assert captured.catalogue_proof.authority_anchor("ruy") == 5
        assert len(semantic_check(prepared(value, captured))) == 1
        return
    assert captured.catalogue_proof.prefix_policies[5] == ["italian", "ruy"]
    assert captured.catalogue_proof.authority_anchor("ruy") is None
    assert captured.catalogue_proof.candidate_route_ids == ["ruy-route"]
    with pytest.raises(RemediationError, match="legacy_route_policy_ambiguous"):
        semantic_check(prepared(value, captured))


def test_two_games_on_one_physical_card_share_reusable_authority_but_have_separate_decision_proofs():
    value, source = source_fixture()
    physical = json.loads(source.rows["cards"][0]["moves_json"]) + ["b5a4", "g8f6"]
    for rows in (source.rows["cards"], source.rows["graphs"]): rows[0]["moves_json"] = json.dumps(physical)
    source.rows["repertoire_lines"][0]["moves_json"] = json.dumps(physical)
    first_data = value.model_dump(mode="json")
    first_data["comparisons"][0]["executable_digest"] = fingerprint(dict(
        starting_fen=source.captured.game["start_fen"], moves=physical, trained_color="black"))
    value = v3.EvidenceDelivery.model_validate(first_data)
    first, first_capture = captured_scope(value, source)
    data = value.model_dump(mode="json")
    decision = decision_identity("physical-black", 7, physical[:7])
    moves = physical[:7] + ["d7d6"]
    source.captured.game.update(id="other-game", provider_game_id="other-provider-game", content_hash="c" * 64, moves_json=json.dumps(moves))
    data.update(game_id="other-game", game_source={**data["game_source"], "content_hash": "c" * 64,
                                                  "provider_game_id": "other-provider-game"})
    data["encounters"][0].update(ply=7, observed_move="d7d6", decision_id=decision,
        position_key=owner.canonical_fen_key(owner.positions(source.captured.game["start_fen"], moves)[7].fen(en_passant="fen")))
    data["comparisons"][0].update(route_ply=7, exercise_ply=7, decision_id=decision, expected_moves=["g8f6"])
    data["misses"][0]["decision_id"] = data["obligations"][0]["decision_id"] = decision
    second, second_capture = captured_scope(v3.EvidenceDelivery.model_validate(data), source)
    assert first.authority == second.authority
    assert first_capture.catalogue_proof.scope != second_capture.catalogue_proof.scope
    for incoming, captured in ((first, first_capture), (second, second_capture)):
        assert semantic_check(prepared(incoming, captured))[0].publication_id == first.authority.storage_identity()[0]


@pytest.mark.parametrize("fault,reason", [("preflight", "preflight_exceeds_budget"), ("eof", "incomplete_exhaustion"),
    ("seek", "seek_not_advanced"), ("time", "deadline"), ("bytes", "row_or_byte_budget"),
    ("candidates", "candidate_budget")])
def test_capture_never_claims_partial_over_budget_or_nonadvancing_exhaustion(fault, reason):
    value, source = source_fixture(filler=130)
    options = {}
    if fault == "preflight": options["budget"] = replace(DEFAULT_BUDGET, max_rows=128)
    if fault == "eof": source.truncate = "repertoire_lines"
    if fault == "seek": source.break_seek = True
    if fault == "time":
        ticks = iter([0.0, 100.0])
        options["clock"] = lambda: next(ticks)
    if fault == "bytes": options["budget"] = replace(DEFAULT_BUDGET, max_bytes=1)
    if fault == "candidates":
        source.rows["repertoire_lines"] = [{**source.rows["repertoire_lines"][0], "id": f"route-{i:03}"} for i in range(65)]
    with pytest.raises(RemediationError, match=reason): captured_scope(value, source, **options)


@pytest.mark.parametrize("fault", ["color", "json", "illegal"])
def test_malformed_or_unknown_color_sources_past_4096_remain_uncertainty(fault):
    value, source = source_fixture(filler=4100)
    row = {**source.rows["repertoire_lines"][0], "id": "zz-uncertain"}
    if fault == "color": row["trained_color"] = None
    if fault == "json": row["moves_json"] = "{invalid"
    if fault == "illegal": row["moves_json"] = '["e2e5"]'
    source.rows["repertoire_lines"].append(row)
    with pytest.raises(RemediationError, match="uncertain_source"): captured_scope(value, source)


@pytest.mark.parametrize("status", ["clean", "needs_repair"])
def test_completed_current_integrity_may_qualify_an_unblocked_full_physical_card(status):
    value, source = source_fixture()
    certificate = integrity_certificate_from_row(integrity_row(status), "ruy", 7)
    source.captured.metadata.update(integrity_status=status, integrity_certificate=certificate)
    value, captured = captured_scope(value, source)
    assert semantic_check(prepared(value, captured))


@pytest.mark.parametrize("field,change", [("integrity_status", "unchecked"), ("scan_status", "running"),
    ("scan_error", "publication cap"), ("checked_at", None), ("scan_completed_sources", 1),
    ("graph_task_generation", 8), ("graph_task_state", "queued"), ("scan_task_state", "queued"),
    ("scan_generation", "old-run"), ("payload_json", '{"repertoire_id":"ruy","graph_generation":6}')])
def test_integrity_owner_refuses_unchecked_incomplete_stale_or_newer_queued_graph(field, change):
    row = integrity_row()
    row[field] = change
    assert integrity_certificate_from_row(row, "ruy", 7) is None


@pytest.mark.parametrize("fault", ["pending", "full_card_block", "unchecked"])
def test_block_at_any_physical_executable_decision_or_unchecked_scan_prevents_acceptance(fault):
    value, source = source_fixture()
    if fault == "pending": source.rows["cards"][0]["pending_validation"] = 1
    if fault == "full_card_block": source.rows["cards"][0]["integrity_blocked"] = True
    if fault == "unchecked": source.captured.metadata.update(integrity_current=False, integrity_certificate=None)
    with pytest.raises(RemediationError):
        incoming, captured = captured_scope(value, source)
        semantic_check(prepared(incoming, captured))


def test_previously_diverged_foreign_route_cannot_reenter_through_matching_later_fen():
    value, source = source_fixture()
    foreign_moves = ["g1f3", "b8c6", "e2e4", "e7e5", "f1b5", "a7a6"]
    actual = json.loads(source.captured.game["moves_json"])
    assert owner.comparison_key(owner.positions(source.captured.game["start_fen"], foreign_moves)[5]) == (
        owner.comparison_key(owner.positions(source.captured.game["start_fen"], actual)[5]))
    source.rows["repertoire_lines"].append(dict(id="zz-transposed-foreign", repertoire_id="italian",
        trained_color="black", start_fen=source.captured.game["start_fen"], moves_json=json.dumps(foreign_moves)))
    value, captured = captured_scope(value, source)
    assert "zz-transposed-foreign" not in captured.catalogue_proof.candidate_route_ids
    assert semantic_check(prepared(value, captured))


@pytest.mark.parametrize("fault", [None, "new_graph_queued", "card_block", "membership_changed"])
def test_worker_acceptance_rechecks_current_integrity_and_full_card_block_after_scoped_capture(monkeypatch, fault):
    value, source = source_fixture()
    value, captured = captured_scope(value, source)
    command = prepared(value, captured)
    writer = Writer(captured)
    if fault == "new_graph_queued":
        writer.captured.metadata["integrity_certificate"].update(graph_task_generation=8, graph_task_state="queued")
    if fault == "card_block":
        execute = writer.execute_native
        def blocked(statement, parameters=()):
            if "FROM repertoire_integrity_card_blocks" in statement:
                writer.events.append(statement)
                return SimpleNamespace(fetchone=lambda: (1,))
            return execute(statement, parameters)
        writer.execute_native = blocked
    if fault == "membership_changed":
        writer.captured.tables["repertoire_cards"].append(dict(
            repertoire_id="italian", card_id="physical-black", canonical_route_source=0))
    fake_repository(monkeypatch, writer)
    result = commands.receive_legacy_delivery(writer, command)
    assert result["status"] == ("completed" if fault is None else "stale_revision")
    assert next(i for i, sql in enumerate(writer.events) if "FOR UPDATE" in sql) < writer.events.index("module lock follows enclosing locks")


def test_unrelated_card_eligibility_or_scan_status_does_not_fork_reusable_repertoire_authority():
    value, source = source_fixture()
    source.rows["cards"].append({**source.rows["cards"][0], "id": "unrelated", "pending_validation": 0})
    first, first_capture = captured_scope(value, source)
    source.rows["cards"][-1]["pending_validation"] = 1
    source.captured.metadata.update(integrity_status="needs_repair", integrity_certificate=(
        integrity_certificate_from_row(integrity_row("needs_repair"), "ruy", 7)))
    second, second_capture = captured_scope(value, source)
    assert first.authority == second.authority
    assert first_capture.catalogue_proof.stream("cards").digest != second_capture.catalogue_proof.stream("cards").digest
    assert semantic_check(prepared(second, second_capture))


def test_read_only_source_inspection_needs_no_obligation_delivery_or_mutating_port():
    from app.services.legacy_game_capture import CaptureRequest, capture_source_scope
    value, source = source_fixture()
    captured = capture_source_scope(CaptureRequest("ruy", value.game_id, (5,)), source.read, source.metadata)
    assert captured.catalogue_proof.scope.decision_plies == [5]
    assert all(re.match(r"(?:/\*.*?\*/ )?SELECT ", sql) for sql, _parameters in source.calls)


def test_integrity_owner_read_projection_performs_only_one_select_and_no_readiness_work():
    from app.services.postgres_integrity import current_integrity_certificate
    from app.postgres_store import TempoRow
    statements = []
    row = integrity_row("needs_repair")
    class ReadOnly:
        def execute_native(self, sql, parameters):
            statements.append(sql)
            assert parameters == ("ruy",) and sql.lstrip().startswith("SELECT")
            return SimpleNamespace(fetchone=lambda: TempoRow(tuple(row), tuple(row.values())))
    assert current_integrity_certificate(ReadOnly(), "ruy", 7)["integrity_status"] == "needs_repair"
    assert len(statements) == 1


def test_acceptance_fences_an_inactive_competing_physical_owner_before_it_can_become_ambiguous(monkeypatch):
    value, source = source_fixture()
    source.rows["cards"].append({**source.rows["cards"][0], "id": "inactive-competitor", "archived": 1})
    source.rows["graphs"].append({**source.rows["graphs"][0], "card_id": "inactive-competitor", "decision_index": 1})
    source.rows["repertoire_cards"].append(dict(repertoire_id="ruy", card_id="inactive-competitor"))
    value, captured = captured_scope(value, source)
    command = prepared(value, captured)
    assert semantic_check(command)
    writer = Writer(captured)
    next(row for row in writer.captured.tables["cards"] if row["id"] == "inactive-competitor")["archived"] = 0
    fake_repository(monkeypatch, writer)
    result = commands.receive_legacy_delivery(writer, command)
    assert result["status"] == "stale_revision"
    card_locks = [sql for sql in writer.events if "SELECT id FROM cards" in sql]
    assert len(card_locks) == 2


@pytest.mark.parametrize("field,value,reason", [("result", "*", "completed_game_required"),
    ("played_at", "2026-10-17", "time_unknown")])
def test_read_only_capture_does_not_claim_applicability_for_an_unfinished_or_untimed_game(field, value, reason):
    request, source = source_fixture()
    source.captured.game[field] = value
    with pytest.raises(RemediationError, match=reason): captured_scope(request, source)
