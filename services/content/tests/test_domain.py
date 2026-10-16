"""Named Content source, legal-universe and immutable identity regressions."""

from copy import deepcopy
import json
from pathlib import Path
import uuid

import chess
from pydantic import ValidationError
import pytest
from tempo_content_publication_contracts.v1 import Publication, serialize_contract, validate_contract

from tempo_content import (ContentError, SourceSnapshot, build_publication,
                           canonical_position, canonical_source, inventory_segment,
                           stable_id, validate_exercise_step)
from tempo_content.models import SourceExercise


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "source.json"
NOW = "2026-10-16T12:00:00Z"


def source_payload():
    return json.loads(FIXTURE.read_text())


def fixture_source():
    return SourceSnapshot.model_validate(source_payload())


def move_exercise(lines=None, mode="single"):
    return SourceExercise(key="exercise", decision_key="decision", policy_key="policy",
                          starting_fen=chess.STARTING_FEN, prompt="Play the move",
                          specification={"type": "move_line", "accepted_lines": lines or [["e2e4"]],
                                         "mode": mode, "grading_policy": "reference"})


def test_issue108_source_fixture_supports_five_kinds_and_policy_separated_shared_route():
    source = fixture_source()
    assert {item.specification.type for item in source.exercises} == {
        "move_line", "square_set", "knight_path", "choice", "explanation"}
    assert {item.learner_color for item in source.repertoires} == {"white", "black"}
    assert len([item for item in source.memberships if item.route_key == "shared-open-game"]) == 2
    for exercise in source.exercises:
        assert validate_exercise_step(exercise) is None


@pytest.mark.parametrize("collection", ["repertoires", "policies", "routes", "exercises", "memberships", "bindings"])
def test_issue108_duplicate_source_entities_and_memberships_are_rejected(collection):
    payload = source_payload()
    payload[collection].append(deepcopy(payload[collection][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        SourceSnapshot.model_validate(payload)


@pytest.mark.parametrize("change", [
    ("policies", "repertoire_key"), ("memberships", "route_key"),
    ("memberships", "policy_key"), ("exercises", "policy_key"),
    ("bindings", "route_key"), ("bindings", "policy_key"), ("bindings", "exercise_key"),
])
def test_issue108_unknown_source_references_are_rejected(change):
    payload = source_payload()
    collection, field = change
    payload[collection][0][field] = "missing"
    with pytest.raises(ValidationError, match="unknown"):
        SourceSnapshot.model_validate(payload)


def test_issue108_membership_and_binding_cannot_cross_policy_ownership():
    payload = source_payload()
    payload["memberships"][0]["repertoire_key"] = "black-repertoire"
    with pytest.raises(ValidationError, match="own its policy"):
        SourceSnapshot.model_validate(payload)
    payload = source_payload()
    payload["bindings"][0]["policy_key"] = "black-policy"
    with pytest.raises(ValidationError, match="own its exercise"):
        SourceSnapshot.model_validate(payload)
    payload = source_payload()
    payload["memberships"] = payload["memberships"][1:]
    with pytest.raises(ValidationError, match="matching route membership"):
        SourceSnapshot.model_validate(payload)


def test_issue108_two_exercises_cannot_ambiguously_own_one_route_policy_ply():
    payload = source_payload()
    payload["bindings"].append({**payload["bindings"][0], "exercise_key": "center-squares"})
    with pytest.raises(ValidationError, match="ambiguous exercise binding"):
        SourceSnapshot.model_validate(payload)


def test_issue108_unmembered_authored_route_cannot_escape_bounded_chess_validation():
    payload = source_payload()
    payload["routes"].append({"key": "unused-invalid-route", "starting_fen": "invalid", "moves": ["e2e5"]})
    with pytest.raises(ValidationError, match="every authored route requires"):
        SourceSnapshot.model_validate(payload)


@pytest.mark.parametrize("change", ["unknown-field", "boolean-version", "boolean-ply", "too-long-route", "binding-after-route"])
def test_issue108_source_is_strict_and_bounds_route_registration(change):
    payload = source_payload()
    if change == "unknown-field":
        payload["routes"][0]["legacy_backend_id"] = "legacy"
    elif change == "boolean-version":
        payload["schema_version"] = True
    elif change == "boolean-ply":
        payload["bindings"][0]["ply"] = False
    elif change == "too-long-route":
        payload["routes"][0]["moves"] = ["g1f3"] * 1001
    else:
        payload["bindings"][0]["ply"] = 4
    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate(payload)


@pytest.mark.parametrize("collection", ["routes", "exercises"])
def test_issue108_source_entity_limit_is_ten_thousand(collection):
    payload = source_payload()
    payload[collection] = [payload[collection][0]] * 10001
    with pytest.raises(ValidationError, match="at most 10000"):
        SourceSnapshot.model_validate(payload)


def test_issue108_shape_validation_defers_chess_to_bounded_worker_and_preserves_historical_supersession():
    payload = source_payload()
    payload["routes"][0]["starting_fen"] = "invalid fen"
    payload["routes"][0]["moves"][0] = "e2e5"
    payload["exercises"][0]["supersedes_key"] = "removed-in-previous-revision"
    assert SourceSnapshot.model_validate(payload).routes[0].starting_fen == "invalid fen"
    payload["exercises"][0]["supersedes_key"] = payload["exercises"][0]["key"]
    with pytest.raises(ValidationError, match="supersede itself"):
        SourceSnapshot.model_validate(payload)


def test_issue108_unordered_source_permutation_has_identical_canonical_payload_and_publication():
    source = fixture_source()
    payload = source_payload()
    for collection in ("repertoires", "policies", "routes", "exercises", "memberships", "bindings"):
        payload[collection].reverse()
    permuted = SourceSnapshot.model_validate(payload)
    assert canonical_source(source) == canonical_source(permuted)
    revisions = {item.key: 1 for item in source.exercises}
    assert serialize_contract(build_publication(source, 1, 1, NOW, revisions)) == serialize_contract(
        build_publication(permuted, 1, 1, NOW, revisions))


def test_issue108_stable_ids_are_scoped_and_unambiguous():
    first = stable_id("workspace", "source", "card", "key")
    assert uuid.UUID(first).version == 5
    assert first == stable_id("workspace", "source", "card", "key")
    assert len({first, stable_id("other", "source", "card", "key"),
                stable_id("workspace", "other", "card", "key"),
                stable_id("workspace", "source", "decision", "key"),
                stable_id("workspace", "source", "card", "other")}) == 5
    assert stable_id("a:b", "c", "card", "d") != stable_id("a", "b:c", "card", "d")


def test_issue108_publication_stable_cards_survive_rename_reorder_and_generation_change():
    source = fixture_source()
    first = build_publication(source, 1, 1, NOW, {item.key: 1 for item in source.exercises})
    payload = source_payload()
    payload["title"] = "Renamed source"
    payload["repertoires"][0]["title"] = "Renamed repertoire"
    payload["routes"].reverse()
    payload["exercises"].append({**deepcopy(payload["exercises"][0]), "key": "unrelated-exercise", "decision_key": "unrelated"})
    second_source = SourceSnapshot.model_validate(payload)
    second = build_publication(second_source, 2, 2, NOW, {item.key: 1 for item in second_source.exercises})
    assert first.publication_id == second.publication_id
    second_by_card = {item.card_id: item for item in second.definitions}
    for exercise in first.definitions:
        current = second_by_card[exercise.card_id]
        assert (exercise.decision_id, exercise.policy_id) == (current.decision_id, current.policy_id)
        assert (current.source_revision, current.publication_generation) == (2, 2)
    assert len({item.card_id for item in first.definitions}) == len(first.definitions)
    black = next(item for item in first.definitions if item.prompt == "Answer White's first move.")
    white = next(item for item in first.definitions if item.prompt == "Play the authored first move.")
    assert black.decision_id != white.decision_id
    assert black.policy_id != white.policy_id


def test_issue108_actual_shared_publication_round_trip_and_revision_validation():
    source = fixture_source()
    revisions = {item.key: 1 for item in source.exercises}
    publication = build_publication(source, 3, 7, NOW, revisions)
    assert isinstance(publication, Publication)
    assert validate_contract(Publication, serialize_contract(publication)) == publication
    assert all(item.exercise_revision == 1 for item in publication.definitions)
    with pytest.raises(ContentError, match="exactly cover"):
        build_publication(source, 3, 7, NOW, {})
    revisions[source.exercises[0].key] = 0
    with pytest.raises(ValidationError):
        build_publication(source, 3, 7, NOW, revisions)


def test_issue108_empty_source_requires_retirement_protocol():
    source = SourceSnapshot(workspace_id="workspace", source_key="source", title="Empty")
    assert source.routes == source.exercises == []
    with pytest.raises(ContentError) as error:
        build_publication(source, 1, 1, NOW, {})
    assert error.value.code == "retirement_required"


@pytest.mark.parametrize("fen", ["", "invalid", "4k3/8/8/8/8/8/8/4K3 w K - 0 1", "8/8/8/8/8/8/8/8 w - - 0 1"])
def test_issue108_invalid_fen_is_actionable(fen):
    with pytest.raises(ContentError) as error:
        canonical_position(fen)
    assert error.value.code == "invalid_fen"


def test_issue108_legal_position_identity_preserves_castling_turn_and_legal_en_passant():
    castling = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    assert canonical_position(castling) != canonical_position(castling.replace(" KQkq ", " - "))
    assert canonical_position(castling) != canonical_position(castling.replace(" w ", " b "))
    assert canonical_position(castling) == canonical_position(castling.replace("0 1", "99 101"))
    ep = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
    assert canonical_position(ep) != canonical_position(ep.replace(" d6 ", " - "))
    pinned = "k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
    assert canonical_position(pinned) == canonical_position(pinned.replace(" d6 ", " - "))


def test_issue108_complete_uncovered_opponent_universe_is_sorted_and_probability_free():
    occurrences, ending_fen = inventory_segment(chess.STARTING_FEN, ["e2e4"], "white")
    assert len(occurrences) == 2 and not occurrences[0]["opponent"]
    opponent = occurrences[1]
    assert opponent["opponent"] and opponent["authored_reply"] is None
    assert opponent["fen_key"] == canonical_position(ending_fen)
    legal_moves = [reply["move_uci"] for reply in opponent["legal_replies"]]
    assert legal_moves == sorted(move.uci() for move in chess.Board(ending_fen).legal_moves)
    assert len(legal_moves) == 20 and "c7c5" in legal_moves
    for reply in opponent["legal_replies"]:
        board = chess.Board(ending_fen)
        board.push_uci(reply["move_uci"])
        assert reply == {"move_uci": reply["move_uci"], "resulting_fen_key": canonical_position(board.fen())}
    black, _ = inventory_segment(chess.STARTING_FEN, ["e2e4"], "black")
    assert black[0]["opponent"] and len(black[0]["legal_replies"]) == 20
    assert not black[1]["opponent"] and black[1]["legal_replies"] == []


@pytest.mark.parametrize("fen,expected", [
    ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", {"e1g1", "e1c1"}),
    ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", {"e5d6"}),
    ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", {"a7a8q", "a7a8r", "a7a8b", "a7a8n"}),
])
def test_issue108_special_legal_move_identities_are_complete(fen, expected):
    occurrences, _ = inventory_segment(fen, [], "black")
    moves = {reply["move_uci"] for reply in occurrences[0]["legal_replies"]}
    assert expected <= moves
    assert moves == {move.uci() for move in chess.Board(fen).legal_moves}


def test_issue108_pinned_en_passant_prohibited_castling_and_terminal_have_exact_support():
    pinned, _ = inventory_segment("k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", [], "black")
    assert "e5d6" not in {reply["move_uci"] for reply in pinned[0]["legal_replies"]}
    checked, _ = inventory_segment("r3k2r/8/8/8/8/8/4r3/R3K2R w KQkq - 0 1", [], "black")
    assert not {"e1g1", "e1c1"} & {reply["move_uci"] for reply in checked[0]["legal_replies"]}
    for fen in ("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"):
        terminal, _ = inventory_segment(fen, [], "white")
        assert terminal[0]["opponent"] and terminal[0]["legal_replies"] == []


def test_issue108_checkpoint_slices_equal_full_route_without_prefix_replay():
    moves = ["g1f3", "g8f6", "f3g1", "f6g8"] * 40 + ["e2e4"]
    whole, whole_fen = inventory_segment(chess.STARTING_FEN, moves, "white")
    checkpoint = chess.STARTING_FEN
    joined = []
    for offset in range(0, len(moves), 16):
        part, checkpoint = inventory_segment(checkpoint, moves[offset:offset + 16], "white",
                                             offset, terminal=offset + 16 >= len(moves))
        joined.extend(part)
    assert joined == whole and checkpoint == whole_fen
    assert [item["ply"] for item in joined] == list(range(len(moves) + 1))


@pytest.mark.parametrize("move", ["e2e5", "not-uci", "e2e4q"])
def test_issue108_illegal_authored_route_moves_do_not_produce_inventory(move):
    with pytest.raises(ContentError) as error:
        inventory_segment(chess.STARTING_FEN, [move], "white")
    assert error.value.code == "illegal_route_move"


def test_issue108_transposed_positions_share_legal_support_with_separate_route_occurrences():
    first = ["g1f3", "g8f6", "g2g3", "g7g6", "f1g2", "f8g7", "e1g1"]
    second = ["g2g3", "g7g6", "g1f3", "g8f6", "f1g2", "f8g7", "e1g1"]
    a, _ = inventory_segment(chess.STARTING_FEN, first, "white")
    b, _ = inventory_segment(chess.STARTING_FEN, second, "white")
    assert a[-1]["fen_key"] == b[-1]["fen_key"]
    assert a[-1]["legal_replies"] == b[-1]["legal_replies"]
    assert a[0]["authored_move"] != b[0]["authored_move"]


def test_issue108_exercise_validation_yields_every_sixteen_plies_and_resumes_from_json(monkeypatch):
    line = ["g1f3", "g8f6", "f3g1", "f6g8"] * 10
    exercise = move_exercise([line, line], "stepwise_line")
    push = chess.Board.push
    push_count = 0
    def counted_push(board, move):
        nonlocal push_count
        push_count += 1
        return push(board, move)
    monkeypatch.setattr(chess.Board, "push", counted_push)
    state = None
    deliveries = 0
    while True:
        before = push_count
        state = validate_exercise_step(exercise, state)
        assert 0 < push_count - before <= 16
        deliveries += 1
        if state is None:
            break
        state = json.loads(json.dumps(state))
    assert push_count == 80 and deliveries == 5


def test_issue108_exercise_mode_and_legality_failures_are_actionable():
    with pytest.raises(ContentError) as error:
        validate_exercise_step(move_exercise([["e2e4", "e7e5"]]))
    assert error.value.code == "invalid_move_mode"
    with pytest.raises(ContentError) as error:
        validate_exercise_step(move_exercise([["e2e5"]]))
    assert error.value.code == "illegal_exercise_move"
    exercise = move_exercise([["g1f3", "g8f6", "f3g1", "f6g8"] * 5], "stepwise_line")
    state = validate_exercise_step(exercise)
    changed = exercise.model_copy(update={"prompt": "Changed immutable exercise"})
    with pytest.raises(ContentError) as error:
        validate_exercise_step(changed, state)
    assert error.value.code == "invalid_validation_checkpoint"


def test_issue108_knight_validation_respects_static_rules_and_unreachable_answers():
    exercise = next(item for item in fixture_source().exercises if item.specification.type == "knight_path")
    assert validate_exercise_step(exercise) is None
    payload = exercise.model_dump(mode="json")
    payload["specification"]["target_squares"] = ["a8"]
    assert validate_exercise_step(SourceExercise.model_validate(payload)) is None
    payload["specification"]["start_square"] = "a1"
    with pytest.raises(ContentError) as error:
        validate_exercise_step(SourceExercise.model_validate(payload))
    assert error.value.code == "invalid_knight_start"
    payload["specification"]["start_square"] = "b1"
    payload["specification"]["target_squares"] = ["a8", "a8"]
    with pytest.raises(ContentError) as error:
        validate_exercise_step(SourceExercise.model_validate(payload))
    assert error.value.code == "duplicate_knight_targets"


@pytest.mark.parametrize("kind", ["square_set", "knight_path", "choice", "explanation"])
def test_issue108_every_non_move_kind_still_requires_a_valid_chess_board(kind):
    exercise = next(item for item in fixture_source().exercises if item.specification.type == kind)
    with pytest.raises(ContentError) as error:
        validate_exercise_step(exercise.model_copy(update={"starting_fen": "invalid"}))
    assert error.value.code == "invalid_fen"
