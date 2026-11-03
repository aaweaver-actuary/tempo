"""Golden examples for stable position identity and mutable answer definition."""
from dataclasses import replace

import chess
import pytest
from tempo_evidence_math import opening_position_key
from tempo_evidence_math.position_learning import (
    DecisionOccurrence,
    PositionDefinition,
    PositionKey,
    RouteOption,
    SourceCertificate,
)

from fixtures.position_identity_examples import (
    CASTLING_INTACT,
    CASTLING_LOST,
    IRRELEVANT_EP,
    ITALIAN_GIUOCO,
    ITALIAN_KNIGHT,
    LEGAL_EP,
    LONDON_ORDER_A,
    LONDON_ORDER_B,
    invalid_fen,
    with_irrelevant_ep,
)


def test_italian_common_trunk_and_knight_giuoco_continuations_are_legal():
    trunk_ply = 5
    assert ITALIAN_KNIGHT.position(trunk_ply) == ITALIAN_GIUOCO.position(trunk_ply)
    assert ITALIAN_KNIGHT.moves_uci[:trunk_ply] == ITALIAN_GIUOCO.moves_uci[:trunk_ply]
    assert ITALIAN_KNIGHT.position(len(ITALIAN_KNIGHT.moves_uci)) != ITALIAN_GIUOCO.position(
        len(ITALIAN_GIUOCO.moves_uci)
    )
    for path in (ITALIAN_KNIGHT, ITALIAN_GIUOCO):
        assert path.board_at(len(path.moves_uci)).is_valid()


def test_london_move_orders_share_exact_position_key_and_keep_repertoire_scope():
    a, b = LONDON_ORDER_A.position(6), LONDON_ORDER_B.position(6)
    assert a == b
    assert LONDON_ORDER_A.moves_uci != LONDON_ORDER_B.moves_uci
    assert PositionKey("london", a) == PositionKey("london", b)
    assert PositionKey("other-repertoire", a) != PositionKey("london", b)


def test_answer_and_policy_changes_preserve_key_but_change_definition():
    key = PositionKey("italian", ITALIAN_KNIGHT.position(5))
    knight = PositionDefinition(key, "g8f6", "policy-1")
    giuoco = PositionDefinition(key, "f8c5", "policy-1")
    revised_policy = PositionDefinition(key, "g8f6", "policy-2")
    assert knight.key == giuoco.key == revised_policy.key
    assert knight != giuoco
    assert knight != revised_policy


def test_position_key_keeps_castling_and_legally_available_en_passant():
    assert CASTLING_INTACT.board_at(0).is_valid()
    assert CASTLING_LOST.board_at(0).is_valid()
    assert CASTLING_INTACT.position(0) != CASTLING_LOST.position(0)
    ep_board = LEGAL_EP.board_at(4)
    assert ep_board.fen(en_passant="fen").split()[3] == "d6"
    assert chess.Move.from_uci("e5d6") in ep_board.legal_moves
    assert LEGAL_EP.position(4) != opening_position_key(
        ep_board.fen(en_passant="fen").replace(" d6 ", " - ")
    )


def test_irrelevant_en_passant_and_move_clocks_do_not_split_identity():
    board = IRRELEVANT_EP.board_at(1)
    raw = with_irrelevant_ep(IRRELEVANT_EP, 1)
    assert raw.split()[3] == "-"
    fen_with_irrelevant_ep = board.fen(en_passant="fen")
    assert fen_with_irrelevant_ep.split()[3] == "e3"
    assert opening_position_key(fen_with_irrelevant_ep) == opening_position_key(raw)

    changed_clocks = raw.rsplit(" ", 2)[0] + " 37 42"
    assert opening_position_key(changed_clocks) == opening_position_key(raw)


def test_invalid_fen_or_illegal_fixture_source_is_rejected():
    with pytest.raises(ValueError, match="invalid_canonical_position_key"):
        PositionKey("italian", invalid_fen())
    with pytest.raises(ValueError, match="illegal_fixture_move"):
        replace(ITALIAN_KNIGHT, moves_uci=("e2e5",)).board_at(1)


def test_incomplete_or_unverified_source_claim_cannot_form_route_option():
    path = ITALIAN_KNIGHT
    decision_ply = 5
    definition = PositionDefinition(
        PositionKey(path.repertoire_id, path.position(decision_ply)), "g8f6", "policy-1"
    )
    decision = DecisionOccurrence(
        decision_ply,
        definition,
        path.board_at(decision_ply).fen(),
    )
    good_source = SourceCertificate("snapshot", "revision", "digest", True, True)
    arguments = dict(
        route_id=path.name,
        start_fen=chess.STARTING_FEN,
        moves_uci=path.moves_uci,
        learner_color="black",
        decisions=(decision,),
        certificate=good_source,
        legal_verified=True,
    )
    with pytest.raises(ValueError, match="route_not_certified"):
        RouteOption(**(arguments | {"certificate": replace(good_source, complete=False)}))
    with pytest.raises(ValueError, match="route_not_certified"):
        RouteOption(**(arguments | {"legal_verified": False}))
