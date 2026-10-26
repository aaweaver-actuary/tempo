"""Regular product-boundary suite: synthetic PGNs, captured values, fake index."""
from copy import deepcopy
import hashlib
import subprocess
import sys
from types import SimpleNamespace

import chess
import chess.pgn
import pytest

from app.contracts.product_v02.games import GameRecord
from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot, RouteBoundary
from app.games.feedback.comparison import compare_record, effect_key, normalize_record
from tempo_game_review_contracts import AuthoredRoute, ReentryAuthority, RepertoireSnapshot as MembershipSnapshot, replay_line

START = chess.STARTING_FEN
TIME = "2026-10-20T12:00:00Z"
DIGEST = "a" * 64


def record(moves=("d2d4",), *, provider="lichess", played_at=TIME, precision="second", starting_fen=START):
    board = chess.Board(starting_fen)
    for move in moves:
        board.push_uci(move)
    game = chess.pgn.Game.from_board(board)
    game.headers.update(White="Learner", Black="Other", Result="1-0")
    pgn = str(game)
    return GameRecord(source_game_id="source-game", source_revision=1,
                      source_digest=hashlib.sha256(pgn.encode()).hexdigest(),
                      pgn=pgn, played_at=played_at, time_precision=precision)


def route(moves=("e2e4", "e7e5", "g1f3"), *, route_id="route", repertoire_id="rep", reentry=None):
    return AuthoredRoute(repertoire_id=repertoire_id, route_id=route_id, policy_id="policy",
                         learner_color="white", starting_fen=START, moves=list(moves),
                         reentries=[] if reentry is None else [ReentryAuthority(ply=reentry, authority_id="anchor")])


def snapshot(*routes, revision=1, deliberate=True, mappings=None, mapping_changes=None, complete=True):
    if not routes:
        routes = (route(),)
    membership = MembershipSnapshot(workspace_id="workspace", snapshot_id="snapshot",
                                    revision=revision, digest=DIGEST, source_revision="source-v1",
                                    captured_at=TIME, scope_id="scope",
                                    scope_repertoire_ids=sorted({item.repertoire_id for item in routes}),
                                    complete=complete, routes=list(routes))
    if mappings is None:
        mappings = []
        for authored in routes:
            for position in replay_line(authored.starting_fen, authored.moves)[:-1]:
                if position["turn"] == "white":
                    fields = dict(decision_id=f"decision-{authored.route_id}-{position['ply']}",
                                  repertoire_id=authored.repertoire_id,
                                  position_key=position["comparison_key"], selected_response_uci=position["move"],
                                  presentation_id=f"card-{authored.route_id}-{position['ply']}",
                                  presentation_revision=1, repertoire_revision=revision,
                                  introduction="introduced", introduced_at="2026-10-01T00:00:00Z",
                                  available=True, introduction_matches_current_content=True)
                    fields.update(mapping_changes or {})
                    mappings.append(DecisionPresentation(**fields))
    return RepertoireSnapshot(membership=membership, variant="standard", learner_color="white",
                              boundaries=tuple(RouteBoundary(route_id=item.route_id,
                                                             terminal_ply=len(item.moves), deliberate=deliberate) for item in routes),
                              decision_presentations=tuple(mappings))


def compare(source=None, captured=None, **kwargs):
    return compare_record(record() if source is None else source,
                          snapshot=snapshot() if captured is None else captured,
                          provider="lichess", account="Learner", workspace_id="workspace", **kwargs)


def test_one_known_introduced_miss():
    finding, = compare()
    assert (finding.kind, finding.ply, finding.expected_uci, finding.played_uci) == ("confirmed_miss", 1, ("e2e4",), "d2d4")
    assert (finding.decision_id, finding.presentation_id) == ("decision-route-0", "card-route-0")
    assert finding.introduced_before_game is True and finding.lapse_eligible


def test_introduced_after_game_is_learning_need_not_lapse():
    finding, = compare(captured=snapshot(mapping_changes={"introduced_at": "2026-10-21T00:00:00Z"}))
    assert finding.kind == "known_unintroduced" and not finding.lapse_eligible


def test_unknown_date_precision_is_not_assumed_before_introduction():
    finding, = compare(source=record(played_at="2026-10-20T00:00:00Z", precision="day"))
    assert finding.kind == "known_unavailable" and not finding.lapse_eligible
    unknown = record(played_at=None, precision="unknown")
    pgn = '[UTCDate "2026.10.20"]\n[UTCTime "12:00:00"]\n' + unknown.pgn
    unknown = unknown.model_copy(update={"pgn": pgn, "source_digest": hashlib.sha256(pgn.encode()).hexdigest()})
    assert normalize_record(unknown, provider="lichess", account="Learner", workspace_id="workspace").played_at is None


def test_changed_content_is_not_historical_introduction_proof():
    finding, = compare(captured=snapshot(mapping_changes={"introduction_matches_current_content": False}))
    assert finding.kind == "known_unavailable" and not finding.lapse_eligible


@pytest.mark.parametrize("changes,kind", [
    ({"introduction": "unintroduced", "introduced_at": None}, "known_unintroduced"),
    ({"introduction": "unknown", "introduced_at": None}, "known_unavailable"),
    ({"available": False}, "known_unavailable"),
])
def test_unintroduced_unknown_and_unavailable_decisions_are_distinct_from_misses(changes, kind):
    finding, = compare(captured=snapshot(mapping_changes=changes))
    assert finding.kind == kind and not finding.lapse_eligible


def test_missing_card_and_incomplete_snapshot_never_prove_a_lapse():
    finding, = compare(captured=snapshot(mappings=[]))
    assert finding.kind == "known_unavailable"
    finding, = compare(captured=snapshot(complete=False))
    assert finding.kind == "known_unavailable"


def test_correct_move_has_no_lapse_without_introduction_evidence():
    captured = snapshot(mapping_changes={"introduction": "unknown", "introduced_at": None})
    assert compare(record(("e2e4",)), captured) == ()


def test_opponent_gap_and_missing_own_continuation_distinct():
    gap, = compare(record(("e2e4", "c7c5", "g1f3")))
    assert (gap.kind, gap.ply) == ("opponent_gap", 2)
    missing, = compare(record(("e2e4", "e7e5", "g1f3")), snapshot(route(("e2e4", "e7e5")), deliberate=False))
    assert (missing.kind, missing.ply) == ("missing_own_continuation", 3)


def test_no_cascade_after_departure():
    findings = compare(record(("d2d4", "d7d5", "g1f3", "g8f6", "c2c4")))
    assert [(item.kind, item.ply) for item in findings] == [("confirmed_miss", 1)]


class FakeReentryIndex:
    def __init__(self, captured, ply, *, source_revision="source-v1", wrong_route=False):
        self.snapshot_id = captured.membership.snapshot_id
        self.source_revision = source_revision
        self.revision = captured.membership.revision
        self.digest = captured.membership.digest
        authored = captured.membership.routes[0]
        self.fen_key = replay_line(authored.starting_fen, authored.moves)[ply]["comparison_key"]
        mapping = next(item for item in captured.decision_presentations if item.position_key == self.fen_key)
        self.candidate = SimpleNamespace(route_id="wrong" if wrong_route else authored.route_id,
                                         ply=ply, source_revision=source_revision, route_complete=True,
                                         binding=mapping, reentry_authority_id="anchor")

    def reentry_candidate(self, repertoire_id, fen, variant="standard"):
        key = replay_line(fen, [])[0]["comparison_key"]
        return self.candidate if repertoire_id == "rep" and key == self.fen_key else None


def test_exact_reentry_resumes_without_erasing_gap():
    # Authored Nf3 d5 g3 c5 Bg2; actual g3 c5 Nf3 d5 d3.
    authored = route(("g1f3", "d7d5", "g2g3", "c7c5", "f1g2"), reentry=4)
    captured = snapshot(authored)
    actual = record(("g2g3", "c7c5", "g1f3", "d7d5", "d2d3"))
    findings = compare(actual, captured, reentry_lookup=FakeReentryIndex(captured, 4))
    assert [(item.kind, item.ply) for item in findings] == [("confirmed_miss", 1), ("confirmed_miss", 5)]
    assert findings[1].reentry_authority_id == "anchor"
    # Legal Bg2 recognizes later coverage and still retains the first deviation.
    legal = record(("g2g3", "c7c5", "g1f3", "d7d5", "f1g2"))
    assert [(item.kind, item.ply) for item in compare(legal, captured, reentry_lookup=FakeReentryIndex(captured, 4))] == [("confirmed_miss", 1)]


def test_opponent_gap_survives_a_later_reentry():
    authored = route(("g1f3", "d7d5", "g2g3", "c7c5", "f1g2"), reentry=4)
    captured = snapshot(authored)
    actual = record(("g1f3", "c7c5", "g2g3", "d7d5", "d2d3"))
    assert [(item.kind, item.ply) for item in compare(actual, captured, reentry_lookup=FakeReentryIndex(captured, 4))] == [("opponent_gap", 2), ("confirmed_miss", 5)]


@pytest.mark.parametrize("bad", [{"source_revision": "stale"}, {"wrong_route": True}])
def test_stale_or_wrong_route_reentry_is_rejected(bad):
    captured = snapshot(route(("g1f3", "d7d5", "g2g3", "c7c5", "f1g2"), reentry=4))
    actual = record(("g2g3", "c7c5", "g1f3", "d7d5", "d2d3"))
    assert len(compare(actual, captured, reentry_lookup=FakeReentryIndex(captured, 4, **bad))) == 1


def test_fen_coincidence_without_explicit_index_authority_does_not_resume():
    captured = snapshot(route(("g1f3", "d7d5", "g2g3", "c7c5", "f1g2"), reentry=4))
    assert len(compare(record(("g2g3", "c7c5", "g1f3", "d7d5", "d2d3")), captured)) == 1


def test_reentry_port_cannot_supply_an_unauthored_anchor_or_stale_card_revision():
    moves = ("g1f3", "d7d5", "g2g3", "c7c5", "f1g2")
    actual = record(("g2g3", "c7c5", "g1f3", "d7d5", "d2d3"))
    captured = snapshot(route(moves))
    assert len(compare(actual, captured, reentry_lookup=FakeReentryIndex(captured, 4))) == 1
    captured = snapshot(route(moves, reentry=4))
    index = FakeReentryIndex(captured, 4)
    index.candidate.binding = index.candidate.binding.model_copy(update={"presentation_revision": 2})
    assert len(compare(actual, captured, reentry_lookup=index)) == 1


def test_terminal_route_is_coverage_end():
    findings = compare(record(("e2e4", "e7e5", "g1f3")), snapshot(route(("e2e4",))))
    assert [(item.kind, item.ply) for item in findings] == [("coverage_end", 2)]


def test_conflicting_own_moves_block_lapse():
    captured = snapshot(route(), route(("d2d4", "d7d5"), route_id="other"))
    finding, = compare(captured=captured)
    assert finding.kind == "conflict" and finding.decision_id is None and not finding.lapse_eligible


def test_conflict_on_a_different_authored_route_is_not_an_accepted_alternative():
    moves = ("g1f3", "d7d5", "g2g3", "c7c5", "f1g2")
    position = replay_line(START, list(moves))[4]["fen"]
    elsewhere = route(("d2d3",), route_id="elsewhere").model_copy(update={"starting_fen": position})
    finding, = compare(record(moves), snapshot(route(moves), elsewhere))
    assert finding.kind == "conflict" and finding.ply == 5
    assert finding.expected_uci == ("d2d3", "f1g2") and not finding.lapse_eligible


def test_ambiguous_repertoire_or_card_not_guessed():
    captured = snapshot()
    other = captured.decision_presentations[0].model_copy(update={"decision_id": "different-decision", "presentation_id": "other-card"})
    ambiguous = captured.model_copy(update={"decision_presentations": (*captured.decision_presentations, other)})
    finding, = compare(captured=ambiguous)
    assert finding.kind == "ambiguous" and finding.presentation_id is None
    cross = snapshot(route(), route(route_id="different-repertoire", repertoire_id="other"))
    findings = compare(captured=cross)
    assert len(findings) == 2 and {item.repertoire_id for item in findings} == {"rep", "other"}
    assert all(item.kind == "ambiguous" and item.decision_id is None for item in findings)


def test_no_matching_start_does_not_guess_one_of_several_repertoires():
    start = replay_line(START, ["e2e4", "e7e5"])[-1]["fen"]
    routes = [route(("g1f3",), route_id=name, repertoire_id=name).model_copy(update={"starting_fen": start})
              for name in ("rep", "other")]
    findings = compare(captured=snapshot(*routes))
    assert {item.repertoire_id for item in findings} == {"rep", "other"}
    assert all(item.kind == "known_unavailable" and item.presentation_id is None for item in findings)


def test_same_game_recomparison_keeps_effect_key():
    first, = compare(captured=snapshot(revision=1))
    later, = compare(captured=snapshot(revision=2))
    assert effect_key(first, workspace_id="workspace").logical_id() == effect_key(later, workspace_id="workspace").logical_id()
    assert first.repertoire_revision != later.repertoire_revision
    reclassified, = compare(captured=snapshot(revision=2, mapping_changes={"introduction_matches_current_content": False}))
    assert reclassified.kind == "known_unavailable"
    assert effect_key(first, workspace_id="workspace").logical_id() == effect_key(reclassified, workspace_id="workspace").logical_id()
    changed_source, = compare(record(("c2c4",)))
    assert changed_source.source_digest != first.source_digest
    assert effect_key(first, workspace_id="workspace").logical_id() == effect_key(changed_source, workspace_id="workspace").logical_id()


def test_input_containers_unchanged():
    source, captured = record(), snapshot()
    before = deepcopy((source.model_dump(), captured.model_dump()))
    compare(source, captured)
    assert (source.model_dump(), captured.model_dump()) == before


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_normalization_preserves_supplied_source_identity_time_and_pgn(provider):
    source = record()
    game = normalize_record(source, provider=provider, account="Learner", workspace_id="workspace")
    assert (game.game_id, game.source_id, game.source_revision, game.source_digest, game.played_at) == (source.source_game_id, provider, source.source_revision, source.source_digest, source.played_at)
    assert game.learner_color == "white" and game.moves == ["d2d4"]
    assert source.pgn == record().pgn


def test_digest_mismatch_is_rejected():
    with pytest.raises(ValueError, match="source_digest"):
        compare(record().model_copy(update={"source_digest": "b" * 64}))


def test_black_player_and_from_position_fen_history_are_preserved():
    start = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 17 23"
    source = record(("g8f6", "b1c3", "b8c6"), starting_fen=start)
    pgn = source.pgn.replace('[White "Learner"]', '[White "Other"]').replace('[Black "Other"]', '[Black "Learner"]')
    source = source.model_copy(update={"pgn": pgn, "source_digest": hashlib.sha256(pgn.encode()).hexdigest()})
    game = normalize_record(source, provider="chesscom", account="Learner", workspace_id="workspace")
    assert game.starting_fen == start and game.learner_color == "black"
    assert game.moves == ["g8f6", "b1c3", "b8c6"]


def test_illegal_or_unsupported_game_is_explicitly_rejected():
    for pgn in ('[White "Learner"]\n[Black "Other"]\n[Result "1-0"]\n\n1. e5 1-0',
                '[Variant "Atomic"]\n[White "Learner"]\n[Black "Other"]\n[Result "1-0"]\n\n1. e4 1-0'):
        source = record().model_copy(update={"pgn": pgn, "source_digest": hashlib.sha256(pgn.encode()).hexdigest()})
        with pytest.raises(ValueError):
            compare(source)


def test_no_network_database_or_study_dependencies():
    # Fresh imports with effects denied, including optional adapter imports.
    script = '''
import builtins, socket, sqlite3, subprocess
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(("app.services", "app.main", "app.repertoire", "app.games.providers", "psycopg", "tempo_intelligence", "tempo_learning")):
        raise AssertionError("forbidden dependency: " + name)
    return original(name, *args, **kwargs)
def denied(*args, **kwargs):
    raise AssertionError("infrastructure access")
builtins.__import__ = guarded
socket.create_connection = denied
socket.socket.connect = denied
socket.socket.connect_ex = denied
sqlite3.connect = denied
subprocess.Popen = denied
from app.games.feedback.comparison import compare_record
from tempo_game_classification import classify_response
assert classify_response(played_uci="d2d4", expected_uci=["e2e4"], learner_turn=True)[0] == "known_unavailable"
'''
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
