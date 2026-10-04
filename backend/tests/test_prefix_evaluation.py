"""Issue 77: production-identical, immutable structural prefix comparisons."""
from dataclasses import replace
import json

import chess
import pytest

from app.services.opening_graph import GraphInput, build_graph
from app.services.cards import card_id
from app.services.prefix_evaluation import (
    EvaluationSnapshot, SourceLine, SplitOverride, PrefixEvaluationError,
    evaluate_prefix, snapshot_identity,
)

CARO_A = ('e2e4', 'c7c6', 'd2d4', 'd7d5', 'e4e5', 'c6c5')
CARO_B = ('e2e4', 'c7c6', 'd2d4', 'd7d5', 'g1f3', 'c8g4')
QGD = ('d2d4', 'd7d5', 'c2c4', 'e7e6', 'g1f3', 'g8f6')


def line(identifier, moves=CARO_A, depth=3, color='black', start=chess.STARTING_FEN):
    return SourceLine(identifier, identifier, start, tuple(moves), color, depth)


def snapshot(lines, splits=()):
    graph_input = GraphInput('rep', tuple(item.graph_line() for item in lines), 6,
                             tuple(item.graph_override() for item in splits))
    return EvaluationSnapshot('rep', 1, tuple(lines), tuple(splits), build_graph(graph_input))


def split(moves, cut, start=chess.STARTING_FEN):
    board = chess.Board(start)
    for move in moves[:cut]: board.push_uci(move)
    return SplitOverride(card_id(start, moves), 1, card_id(start, moves[:cut]),
                         start, tuple(moves[:cut]), 1,
                         card_id(board.fen(), moves[cut:]), board.fen(), tuple(moves[cut:]), 1)


def test_issue77_current_depths_reproduce_production_graph_and_do_not_mutate_input():
    source = snapshot((line('a'), line('qgd', QGD, 2)))
    before = snapshot_identity(source)
    result = evaluate_prefix(source, ('qgd', 'a'), None)
    assert result['status'] == 'no_change'
    assert not result['depth_configuration_changed'] and not result['structure_changed']
    assert result['whole_repertoire']['current'] == result['whole_repertoire']['proposed']
    assert result['current_depth_distribution'] == [{'depth': 2, 'line_count': 1}, {'depth': 3, 'line_count': 1}]
    assert result['whole_repertoire']['current']['steps'] == [step_projection(step) for step in source.published_steps]
    assert snapshot_identity(source) == before
    assert evaluate_prefix(source, ('a', 'qgd'), None) == result


def step_projection(step):
    from dataclasses import asdict
    return json.loads(json.dumps(asdict(step)))


def test_issue77_black_shortening_has_hand_checked_counts_without_alias_inflation():
    source = snapshot((line('a'), line('b', CARO_B)))
    result = evaluate_prefix(source, ('a', 'b'), {'a': 2, 'b': 2})
    before = result['selected']['current']['metrics']
    after = result['selected']['proposed']['metrics']
    assert (before['distinct_cards'], before['learner_decision_occurrences'], before['repeated_decisions_across_cards']) == (2, 6, 2)
    assert (after['distinct_cards'], after['prefix_cards'], after['descendant_decision_cards'], after['learner_decision_occurrences']) == (3, 1, 2, 4)
    assert after['repeated_decisions_across_cards'] == 0
    assert result['selected']['additional_starts'] == 1


def test_issue77_unselected_alias_retains_card_and_qgd_route_unchanged():
    source = snapshot((line('a'), line('b', CARO_B), line('alias'), line('qgd', QGD)))
    result = evaluate_prefix(source, ('a', 'b'), {'a': 2, 'b': 2})
    assert result['selected']['proposed']['metrics']['distinct_cards'] == 3
    assert result['whole_repertoire']['proposed']['metrics']['distinct_cards'] == 5
    assert card_id(chess.STARTING_FEN, CARO_A) in result['whole_repertoire']['unchanged_card_ids']
    for identifier in ('alias', 'qgd'):
        before = [step for step in result['whole_repertoire']['current']['steps'] if step['line_id'] == identifier]
        after = [step for step in result['whole_repertoire']['proposed']['steps'] if step['line_id'] == identifier]
        assert before == after


def test_issue77_mixed_depths_and_duplicate_aliases_use_saved_depths():
    source = snapshot((line('a', depth=2), line('alias', depth=2), line('b', CARO_B)))
    result = evaluate_prefix(source, ('a', 'alias', 'b'), None)
    assert result['selected']['current']['metrics']['distinct_cards'] == 3
    assert result['current_depth_distribution'] == [{'depth': 2, 'line_count': 2}, {'depth': 3, 'line_count': 1}]


def test_issue77_custom_black_root_and_opponent_cues_count_decisions_not_plies():
    board = chess.Board(); board.push_uci('e2e4')
    source = snapshot((line('custom', CARO_A[1:], 2, start=board.fen()),))
    result = evaluate_prefix(source, ('custom',), None)
    assert result['selected']['current']['metrics']['learner_decision_occurrences'] == 3
    assert result['selected']['current']['metrics']['distinct_cards'] == 2
    cue_source = snapshot((line('e5', ('e2e4', 'e7e5', 'g1f3'), 1, 'white'),
                           line('c5', ('e2e4', 'c7c5', 'g1f3'), 1, 'white')))
    assert evaluate_prefix(cue_source, ('e5', 'c5'), None)['selected']['current']['metrics']['distinct_cards'] == 3


def test_issue77_chained_saved_splits_report_effective_depth_and_honor_production():
    overrides = (split(CARO_A, 4), split(CARO_A[:4], 2))
    source = snapshot((line('a'),), overrides)
    result = evaluate_prefix(source, ('a',), {'a': 3})
    assert result['line_depths'] == [{'line_id': 'a', 'current_depth': 3, 'requested_depth': 3,
                                    'current_effective_depth': 1, 'proposed_effective_depth': 1}]
    assert result['selected']['proposed']['metrics']['distinct_cards'] == 3
    assert result['status'] == 'no_change'


def test_issue77_short_routes_empty_selection_and_no_learner_moves_are_distinct():
    source = snapshot((line('short', CARO_A[:2], 3),))
    result = evaluate_prefix(source, ('short',), {'short': 2})
    assert result['depth_configuration_changed'] and not result['structure_changed']
    assert result['status'] == 'no_change'
    empty = evaluate_prefix(source, (), {})
    assert empty['status'] == 'empty_selection' and empty['selected_line_count'] == 0
    assert empty['whole_repertoire']['current'] == empty['whole_repertoire']['proposed']
    opponent = snapshot((line('opponent', ('e2e4',), 2),))
    assert evaluate_prefix(opponent, ('opponent',), None)['selected']['current']['metrics']['distinct_cards'] == 0


def test_issue77_cycles_and_overlapping_roles_preserve_distinct_card_accounting():
    cycle = snapshot((line('cycle', ('g1f3', 'g8f6', 'f3g1', 'f6g8', 'g1f3'), 3, 'white'),))
    metrics = evaluate_prefix(cycle, ('cycle',), None)['selected']['current']['metrics']
    assert metrics['learner_decision_occurrences'] == 3
    assert metrics['distinct_learner_decisions'] == 2 and metrics['repeated_decisions_within_cards'] == 1
    board = chess.Board()
    for move in CARO_A[:4]: board.push_uci(move)
    overlapping = snapshot((line('full', depth=2), line('interior', CARO_A[4:], 1, start=board.fen())))
    presentation = evaluate_prefix(overlapping, ('full', 'interior'), None)['selected']['current']
    assert presentation['metrics']['distinct_cards'] == 2
    assert presentation['metrics']['prefix_cards'] == 2 and presentation['metrics']['descendant_decision_cards'] == 1
    assert ['decision', 'prefix'] in [card['roles'] for card in presentation['cards']]


@pytest.mark.parametrize('selection,depths', [(['a', 'a'], None), (['foreign'], None), (['a'], {}),
    (['a'], {'a': 0}), (['a'], {'a': 21}), (['a'], {'a': True}), (['a'], {'a': 2, 'other': 3})])
def test_issue77_invalid_selection_and_candidate_depths_are_actionable(selection, depths):
    with pytest.raises(PrefixEvaluationError) as error:
        evaluate_prefix(snapshot((line('a'),)), selection, depths)
    assert error.value.code == 'invalid_selection'


@pytest.mark.parametrize('depth', [None, 0, -1])
def test_issue77_missing_or_zero_saved_depth_never_uses_global_default(depth):
    source = EvaluationSnapshot('rep', 1, (line('a', depth=depth),), (), ())
    with pytest.raises(PrefixEvaluationError) as error: evaluate_prefix(source, ('a',), None)
    assert error.value.code == 'unsupported_source'


def test_issue77_stale_graph_and_malformed_split_fail_without_partial_results():
    source = snapshot((line('a'),))
    with pytest.raises(PrefixEvaluationError) as error:
        evaluate_prefix(replace(source, published_steps=()), ('a',), None)
    assert error.value.code == 'graph_not_ready'
    malformed = replace(split(CARO_A, 4), shortened_card_id='wrong')
    with pytest.raises(PrefixEvaluationError) as error:
        evaluate_prefix(replace(source, prefix_overrides=(malformed,)), ('a',), None)
    assert error.value.code == 'unsupported_source'


def test_issue77_size_limits_fail_without_truncating_source(monkeypatch):
    from app.services import prefix_evaluation as evaluator
    source = snapshot((line('a'), line('b', CARO_B)))
    for bound in ('MAX_SOURCE_LINES', 'MAX_SOURCE_PLIES', 'MAX_GRAPH_STEPS'):
        with monkeypatch.context() as scope:
            scope.setattr(evaluator, bound, 1)
            with pytest.raises(PrefixEvaluationError) as error: evaluate_prefix(source, ('a',), None)
            assert error.value.code == 'limit_exceeded'


def test_issue77_snapshot_binds_depth_source_graph_and_split_revisions():
    source = snapshot((line('a'),), (split(CARO_A, 4),))
    original = snapshot_identity(source)
    for changed in (replace(source, graph_generation=2), replace(source, repertoire_id='other'),
                    replace(source, lines=(replace(source.lines[0], saved_depth=2),)),
                    replace(source, lines=(replace(source.lines[0], moves=CARO_B),)),
                    replace(source, prefix_overrides=(replace(source.prefix_overrides[0], shortened_revision=2),))):
        assert snapshot_identity(changed) != original
