"""Complete authored scope, exact-game replay and explicit candidate bindings."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import chess
import pytest

from app.repertoire import PreparedFirstBoundaryRepertoire, project_published_repertoire
from app.repertoire.feedback_scope import _position
from app.games.feedback.first_boundary import select_first_boundary
from app.contracts.product_v02.repertoire import IntroductionGameScope
from tempo_evidence_math import opening_position_key
from test_product_first_boundary import game
from test_product_snapshot import repertoire_facts, route_reuse_facts

NOW = '2026-10-30T00:00:00Z'


def authored(facts):
    return dict(repertoire=facts['repertoire'], lines=facts['lines'],
        card_count=len(facts['cards']), membership_count=len(facts['memberships']),
        graph_count=len(facts['graphs']))


def candidate(facts, route_ids=None):
    route_ids = sorted(route_ids or [row['id'] for row in facts['lines']])
    graphs = [row for row in facts['graphs'] if row['line_id'] in route_ids]
    card_ids = {row['card_id'] for row in graphs}
    return dict(repertoire=facts['repertoire'], route_ids=route_ids, graphs=graphs,
        cards=[row for row in facts['cards'] if row['id'] in card_ids],
        memberships=[row for row in facts['memberships'] if row['card_id'] in card_ids])


def select(current, snapshots, prepared=(), **kwargs):
    source = IntroductionGameScope(provider='lichess', game_id=current.game_id,
        source_revision=current.source_revision, source_digest=current.source_digest)
    return select_first_boundary(current, snapshots=snapshots, source=source,
        prepared_repertoires=prepared, **kwargs)


def policy_value(selection):
    value = selection.model_dump(mode='json')
    for scope in value['selected_scope']:
        for field in ('snapshot_id', 'source_revision', 'digest'):
            scope[field] = '<owner-specific-source-provenance>'
    for route in value['surviving_routes']:
        route['source_revision'] = '<owner-specific-source-provenance>'
    return value


@pytest.mark.parametrize('variant', [
    'base', 'repeated', 'unintroduced', 'unknown', 'unavailable', 'conflict',
    'ambiguous', 'illegal-route', 'illegal-card', 'unused-illegal-route',
])
@pytest.mark.parametrize('moves', [('d2d4',), ('e2e4', 'c7c5'), ('e2e4', 'e7e5', 'g1f3')])
def test_game_bound_scope_preserves_full_selector_policy_and_mapping(variant, moves):
    facts = route_reuse_facts(variant)
    current = game(moves)
    full = project_published_repertoire(facts, captured_at=NOW)
    prepared = PreparedFirstBoundaryRepertoire(authored(facts), game=current, captured_at=NOW)
    prepared = prepared.with_bindings(candidate(facts))
    actual = select(current, (prepared.snapshot,), (prepared,))
    assert policy_value(actual) == policy_value(select(current, (full,)))
    assert prepared.snapshot.membership.scope_id.startswith('first-boundary:')
    assert prepared.snapshot.membership.digest != full.membership.digest


def test_game_bound_partial_candidate_cannot_manufacture_unique_mapping():
    facts = repertoire_facts()
    facts['lines'].append(facts['lines'][0] | dict(id='unread-alias'))
    facts['graphs'].append(facts['graphs'][0] | dict(line_id='unread-alias'))
    current = game()
    prepared = PreparedFirstBoundaryRepertoire(authored(facts), game=current)
    incomplete = prepared.with_bindings(candidate(facts, ['route']))
    result = select(current, (incomplete.snapshot,), (incomplete,))
    assert result.status == 'own_unmapped' and result.decision is None
    assert {row.route_id for row in result.surviving_routes} == {'route', 'unread-alias'}
    complete = prepared.with_bindings(candidate(facts))
    assert select(current, (complete.snapshot,), (complete,)).status == 'own_actionable'


def test_game_bound_value_owns_source_and_rejects_wrong_game_or_forged_scope():
    facts, current = repertoire_facts(), game()
    original = deepcopy(facts)
    prepared = PreparedFirstBoundaryRepertoire(authored(facts), game=current)
    prepared = prepared.with_bindings(candidate(facts))
    before = prepared.snapshot.model_dump(mode='json')
    facts['cards'][0]['archived'] = 1
    prepared.candidate_facts()['cards'][0]['archived'] = 1
    prepared.authored_facts()['lines'].clear()
    assert prepared.snapshot.model_dump(mode='json') == before
    assert prepared.candidate_facts()['cards'] == original['cards']
    with pytest.raises(FrozenInstanceError):
        prepared.snapshot = project_published_repertoire(facts)
    with pytest.raises(TypeError):
        prepared.work['replayed_nodes'] = 0
    for changed in (current.model_copy(update={'game_id': 'other'}),
                    current.model_copy(update={'source_revision': 2}),
                    current.model_copy(update={'moves': ['c2c4']})):
        with pytest.raises(ValueError, match='prepared_scope_mismatch'):
            select(changed, (prepared.snapshot,), (prepared,))
    forged = prepared.snapshot.model_copy(update={'learner_color': 'black'})
    with pytest.raises(ValueError, match='prepared_scope_mismatch'):
        select(current, (forged,), (prepared,))
    with pytest.raises(ValueError, match='prepared_scope_mismatch'):
        select(current, (prepared.snapshot,), (object(),))


@pytest.mark.parametrize('fen,moves', [
    (chess.STARTING_FEN, ['e2e4', 'e7e5']),
    ('4k3/8/8/3pP3/8/8/8/4K3 w - d6 17 42', ['e5d6', 'e8d7']),
    ('4r1k1/8/8/3pP3/8/8/8/4K3 w - d6 13 11', ['e5e6', 'g8f8']),
    ('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 4 17', ['e1g1', 'e8c8']),
])
def test_numeric_position_identity_matches_exact_legal_ep_fen(fen, moves):
    board = chess.Board(fen)
    for move in [*moves, None]:
        raw = board.fen(en_passant='fen')
        normalized = board.fen(en_passant='legal')
        assert _position(board) == _position(chess.Board(normalized))
        assert opening_position_key(raw) == opening_position_key(normalized)
        if board.ep_square is not None and board.has_legal_en_passant():
            without_ep = board.copy(stack=False)
            without_ep.ep_square = None
            assert _position(board) != _position(without_ep)
        changed_clock = board.copy(stack=False)
        changed_clock.halfmove_clock += 42
        changed_clock.fullmove_number += 42
        assert _position(board) == _position(changed_clock)
        if move is not None:
            board.push_uci(move)


def test_game_bound_prefix_replay_keeps_aliases_and_enforces_inner_work_accounting():
    facts = repertoire_facts()
    cycle = ['g1f3', 'g8f6', 'f3g1', 'f6g8'] * 40
    facts['lines'] = [facts['lines'][0] | dict(id='alias-' + str(i), moves_json=json.dumps(cycle))
                      for i in range(10)]
    prepared = PreparedFirstBoundaryRepertoire(authored(facts), game=game())
    assert prepared.work['input_plies'] == 1600
    assert prepared.work['prefix_nodes'] == prepared.work['replayed_nodes'] == 160
    assert prepared.work['accounting_interval_nodes'] == 128
    assert prepared.work['accounting_batches'] >= 13
    assert len(prepared.snapshot.membership.routes) == 10
    assert prepared.work['elapsed_seconds'] >= 0
    bad = authored(facts) | dict(card_count=10001)
    with pytest.raises(ValueError, match='repertoire_snapshot_exceeds_bound'):
        PreparedFirstBoundaryRepertoire(bad, game=game())


def test_game_bound_header_change_and_graph_outside_requested_routes_fail_closed():
    facts, current = repertoire_facts(), game()
    prepared = PreparedFirstBoundaryRepertoire(authored(facts), game=current)
    changed = candidate(facts)
    changed['repertoire'] = changed['repertoire'] | dict(graph_generation=99)
    with pytest.raises(ValueError, match='selected_publication_changed'):
        prepared.with_bindings(changed)
    changed = candidate(facts)
    changed['graphs'] = [changed['graphs'][0] | dict(line_id='not-requested')]
    with pytest.raises(ValueError, match='candidate_routes_changed'):
        prepared.with_bindings(changed)


def test_game_bound_projected_frames_are_read_only_and_exact_aliases_share_execution():
    facts, current = repertoire_facts(), game()
    facts['lines'].append(facts['lines'][0] | dict(id='alias'))
    prepared = PreparedFirstBoundaryRepertoire(authored(facts), game=current, captured_at=NOW)
    routes = prepared.snapshot.membership.routes
    frames = prepared.replay_route(current, routes[0])
    assert frames is prepared.replay_route(current, routes[1])
    with pytest.raises(TypeError):
        frames[0]['comparison_key'] = 'forged'
    with pytest.raises(ValueError, match='prepared_scope_mismatch'):
        prepared.replay_route(current, routes[0].model_copy(update={'moves': ('d2d4',)}))
    reordered = deepcopy(facts)
    reordered['lines'].reverse()
    same = PreparedFirstBoundaryRepertoire(authored(reordered), game=current, captured_at=NOW)
    assert prepared.snapshot.membership == same.snapshot.membership


def test_study_owner_rejects_producer_mapping_substitution_despite_matching_route_execution():
    from types import SimpleNamespace
    from app.contracts.product_v02.learning import GameEncounter, GameEffectKey, FirstDepartureWitness
    from tempo_game_evidence_contracts.legacy import fingerprint
    from tempo_legacy_database.adapters.study.game_feedback_authority import (
        CurrentOwnerDecisions, CurrentDecisionUnavailable)
    facts = repertoire_facts()
    current = game()
    raw = dict(id=current.game_id, provider='lichess', start_fen=current.starting_fen,
        moves_json=json.dumps(current.moves), color='white', played_at=current.played_at)
    current = current.model_copy(update={'source_digest': fingerprint(raw)})
    prepared = PreparedFirstBoundaryRepertoire(authored(facts), game=current)
    prepared = prepared.with_bindings(candidate(facts))
    real = select(current, (prepared.snapshot,), (prepared,))
    original = GameEncounter(key=GameEffectKey(workspace_id=current.workspace_id,
        provider='lichess', game_id=current.game_id, decision_id=real.decision.decision_id,
        occurrence_ply=real.ply), source=real.source, decision=real.decision,
        expected_uci=real.expected_uci[0], played_uci=real.played_uci, observation='missed',
        detected_at=NOW, occurred_at=current.played_at, payload_digest=current.source_digest)
    altered = prepared.snapshot.model_copy(update={'decision_presentations': tuple(
        item.model_copy(update={'presentation_id': 'producer-forged-card'})
        for item in prepared.snapshot.decision_presentations)})
    supplied = select(current, (altered,), (prepared,))
    assert supplied.status == 'own_actionable' and supplied.decision.presentation_id == 'producer-forged-card'
    witness = FirstDepartureWitness(selection=supplied, configuration_revision=1,
        determined_at=NOW, stream='forward')
    assert witness.matches(original)  # Matching occurrence alone grants no physical authority.
    owner = object.__new__(CurrentOwnerDecisions)
    owner.database = None
    owner.authority = SimpleNamespace(workspace_id=current.workspace_id)
    owner.source = dict(game=raw, tables=dict(cards=facts['cards']))
    owner._validate_executable = lambda *args, **kwargs: pytest.fail('forged selection reached target preparation')
    def independent(game, **kwargs):
        return select_first_boundary(game, prepared_repertoires=(prepared,), **kwargs)
    with pytest.raises(CurrentDecisionUnavailable, match='first_departure_selection_changed'):
        owner.prepare_qualified(original, witness, qualified_publications=(prepared,),
            qualification_fence=lambda *args: None, game=current, select_first_boundary=independent)



def test_repertoire_first_boundary_interface_is_exact_and_cold_imports_only_pure_owners():
    import os
    from pathlib import Path
    import subprocess
    import sys
    from app.repertoire.interfaces import first_boundary
    from app.repertoire import feedback_scope
    assert first_boundary.__all__ == ['PreparedFirstBoundaryRepertoire']
    assert first_boundary.PreparedFirstBoundaryRepertoire is feedback_scope.PreparedFirstBoundaryRepertoire
    assert first_boundary.PreparedFirstBoundaryRepertoire is PreparedFirstBoundaryRepertoire
    assert first_boundary.PreparedFirstBoundaryRepertoire.__dataclass_params__.frozen
    script = """
import importlib.abc, sys
import chess, pydantic
blocked = ('app.games', 'app.study', 'app.services', 'app.product_composition',
    'app.main', 'app.tasks', 'tempo_legacy_database', 'psycopg', 'sqlite3',
    'requests', 'httpx', 'fastapi', 'chess.engine')
class PureOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(blocked):
            raise AssertionError('public facade imported runtime: ' + fullname)
sys.meta_path.insert(0, PureOnly())
from app.repertoire.interfaces import first_boundary as api
from app.repertoire.feedback_scope import PreparedFirstBoundaryRepertoire
assert api.__all__ == ['PreparedFirstBoundaryRepertoire']
assert api.PreparedFirstBoundaryRepertoire is PreparedFirstBoundaryRepertoire
assert not any(name.startswith(blocked) for name in sys.modules)
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, '-B', '-c', script], cwd=root,
        env=os.environ.copy(), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
