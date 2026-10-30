"""Owning acceptance-harness checks; no database or Docker allocation."""
import pytest
import chess

from test_product_game_lapse_postgres import postgres_fixture_profile, study_recovery_assembly_allocations
from fixtures.study_recovery_content import Shape, publication


def test_recovery_profile_keeps_default_native_allocation_and_is_explicit():
    assert postgres_fixture_profile('default') == dict(memory_mib=384, storage='tmpfs')
    assert postgres_fixture_profile('study-recovery') == dict(memory_mib=1024, storage='owned-volume')
    assert postgres_fixture_profile('study-recovery-assembly') == dict(memory_mib=384, storage='owned-volume')
    for unsupported in (None, '', 'live', '1024', 'unlimited'):
        with pytest.raises(ValueError, match='unknown_owned_postgres_fixture_profile'):
            postgres_fixture_profile(unsupported)


def test_recovery_publication_is_legal_complete_short_card_scope_with_real_standard_target():
    shape = Shape('white', 12, 216, 33, 48, 84, 216, 7)
    generated = publication(shape, 'rep', 'fixture')
    assert generated['generated']['routes'] == shape.routes
    assert generated['generated']['authored_plies'] == shape.authored_plies
    assert generated['generated']['graphs'] == shape.graphs
    assert generated['target']['starting_fen'] == chess.STARTING_FEN
    assert generated['target']['occurrence_ply'] == 9
    own = {}
    import json
    for row in generated['lines']:
        board = chess.Board(row['start_fen'])
        for uci in json.loads(row['moves_json']):
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            if board.turn:
                key = ' '.join(board.fen(en_passant='legal').split()[:4])
                assert own.setdefault(key, uci) == uci
            board.push(move)
    cards = {row['id']: row for row in generated['cards']}
    for graph in generated['graphs']:
        assert graph['moves_json'] == cards[graph['card_id']]['moves_json']
        board = chess.Board(graph['starting_fen'])
        for uci in json.loads(graph['moves_json']):
            board.push_uci(uci)


def test_recovery_standard_target_is_actionable_across_complete_distinct_repertoire_scope():
    """A real target survives complete scope; competing physical owners stay distinct."""
    import json
    from app.repertoire import PreparedPublishedRepertoire
    from app.games.feedback.first_boundary import select_first_boundary
    from app.contracts.product_v02.repertoire import IntroductionGameScope
    from tempo_game_review_contracts import CanonicalGame
    from test_product_snapshot import repertoire_facts

    snapshots, ids, physical, target = [], [], {}, None
    colors = ('white', 'white', 'black', 'white', 'white', 'white', 'white', 'black', 'white', 'black')
    for ordinal, color in enumerate(colors):
        repertoire = f'rep-{ordinal}'
        shape = Shape(color, 12, 216, 33 if color == 'white' else 32, 48, 84, 216, 7)
        content = publication(shape, repertoire, 'fixture', ordinal=ordinal)
        template = repertoire_facts()
        facts = dict(repertoire=template['repertoire'] | dict(id=repertoire, graph_generation=1),
            lines=content['lines'],
            cards=[template['cards'][0] | row | dict(revision=1) for row in content['cards']],
            memberships=[dict(repertoire_id=repertoire, card_id=row['id'], canonical_route_source=1)
                         for row in content['cards']],
            graphs=[row | dict(generation=1) for row in content['graphs']])
        for row in content['cards']:
            geometry = (row['start_fen'], row['moves_json'], row['trained_color'])
            assert physical.setdefault(row['id'], geometry) == geometry

        snapshots.append(PreparedPublishedRepertoire(facts).snapshot)
        ids.append(repertoire)
        target = target or content['target']
        assert len(facts['lines']) == shape.routes and len(facts['graphs']) == shape.graphs
        assert sum(len(json.loads(row['moves_json'])) for row in facts['lines']) == shape.authored_plies
    game = CanonicalGame(workspace_id='fixture', game_id='standard-root', game_revision=1,
        source_id='lichess', source_revision=1, source_digest='d' * 64,
        starting_fen=target['starting_fen'], moves=[*target['prefix'], target['wrong']],
        learner_color='white', played_at='2026-10-29T00:00:00Z')
    source = IntroductionGameScope(provider='lichess', game_id=game.game_id,
        source_revision=1, source_digest=game.source_digest)
    selected = select_first_boundary(game, snapshots=snapshots, source=source, selected_repertoire_ids=ids)
    assert selected.status == 'own_actionable'
    assert selected.ply == 9 and selected.expected_uci == (target['expected'],)
    assert selected.decision.presentation_id == target['card_id']
    assert selected.decision.repertoire_id == 'rep-0'
    assert {item.repertoire_id for item in selected.selected_scope} == {
        f'rep-{index}' for index, color in enumerate(colors) if color == 'white'}


def test_recovery_graph_uses_real_owner_geometry_and_contiguous_frontier_metadata():
    import json
    from app.contracts.opening_graph import GraphStep
    from app.services.opening_frontier import (
        OpeningFrontierCard, OpeningFrontierPublication, OpeningFrontierReview,
        OpeningFrontierRoute, OpeningFrontierSnapshot, project_opening_frontier,
    )
    for color in ('white', 'black'):
        shape = Shape(color, 12, 216, 33 if color == 'white' else 32, 48, 84, 216, 7)
        content = publication(shape, 'rep', 'fixture')
        owner = OpeningFrontierPublication('rep', 1, 'fixture')
        routes = []
        for line in content['lines']:
            rows = [row for row in content['graphs'] if row['line_id'] == line['id']]
            steps = tuple(GraphStep(repertoire_id='rep', line_id=line['id'],
                decision_index=row['decision_index'], segment_kind=row['segment_kind'],
                first_decision_index=row['first_decision_index'], last_decision_index=row['last_decision_index'],
                decision_fen_keys=tuple(json.loads(row['decision_fen_keys_json'])), card_id=row['card_id'],
                parent_card_id=row['parent_card_id'], decision_fen_key=row['decision_fen_key'],
                starting_fen=row['starting_fen'], moves=tuple(json.loads(row['moves_json'])), trained_color=color)
                for row in rows)
            routes.append(OpeningFrontierRoute(owner, line['id'], steps))
        snapshot = OpeningFrontierSnapshot('fixture', (owner,), tuple(routes),
            tuple(OpeningFrontierCard(row['id'], ('rep',), introduced_at='2026-08-01') for row in content['cards']),
            tuple(OpeningFrontierReview('review-' + row['id'], row['id']) for row in content['cards']))
        frontier = project_opening_frontier(snapshot)
        assert len(frontier.cards) == len(content['cards'])
        assert all(not route.blocked_reasons and not route.missing_prerequisite_card_ids
                   for card in frontier.cards for route in card.routes)


def test_recovery_observed_shapes_keep_exact_route_graph_counts_and_explicit_payload_deltas():
    from fixtures.study_recovery_content import OBSERVED, _route_lengths, _prefix_decisions
    authored = graphs = graph_plies = 0
    for ordinal, shape in enumerate(OBSERVED):
        custom = ordinal == 0
        lengths = _route_lengths(shape, existing_custom=custom)
        prefixes = _prefix_decisions(shape, lengths, existing_custom=custom)
        own = sum((length + (shape.color == 'white')) // 2 for length in lengths)
        assert len(lengths) + custom == shape.routes
        assert sum(lengths) + custom == shape.authored_plies
        assert max(lengths) == shape.max_route_plies
        assert own - sum(prefixes) + len(lengths) + custom == shape.graphs
        payload = sum(2 * ((length + (shape.color == 'white')) // 2) - (shape.color == 'white')
                      for length in lengths) + custom
        assert payload - shape.graph_plies == (-1 if ordinal in (4, 5) else 0)
        oversize = [value for value in prefixes if 2 * value - (shape.color == 'white') > shape.max_card_plies]
        assert len(oversize) == (2 if custom else 0)
        assert max(2 * value - (shape.color == 'white') for value in prefixes) == shape.max_card_plies + (2 if custom else 0)
        authored += sum(lengths) + custom
        graphs += own - sum(prefixes) + len(lengths) + custom
        graph_plies += payload
    assert (authored, graphs, graph_plies) == (242235, 94929, 242138)


def test_recovery_assembly_allocation_fits_existing_budget_without_changing_global_roles():
    from copy import deepcopy
    import json
    from test_product_game_lapse_postgres import ROOT
    configuration = json.loads((ROOT / 'config/resource-budgets.json').read_text())
    before = deepcopy(configuration)
    selected = study_recovery_assembly_allocations(configuration)
    assert {role: value['memoryMiB'] for role, value in selected.items()} == {
        'postgres': 384, 'redis': 64, 'api': 192, 'foreground-worker': 384,
        'background-worker': 448, 'game-feedback-worker': 512, 'web': 64}
    assert sum(value['memoryMiB'] for value in selected.values()) == 2048
    assert configuration == before
    smaller = deepcopy(configuration)
    smaller['development']['memoryMiB'] = 2047
    with pytest.raises(AssertionError, match='study_recovery_assembly_exceeds_development_budget'):
        study_recovery_assembly_allocations(smaller)
