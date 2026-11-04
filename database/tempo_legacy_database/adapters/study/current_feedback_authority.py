"""Exact current feedback target authority, independent of legacy export formats.

The caller supplies source-owned complete configured publications and the
canonical game. Closed reads retain only one physical target, its memberships,
graph uses and current integrity facts; final acceptance fences those facts.
"""
from __future__ import annotations

import json

import chess
from tempo_chess_core import canonical_fen_key
from tempo_game_evidence_contracts.legacy import decision_identity
from tempo_game_review_contracts import CanonicalGame

from app.study.game_feedback import instant
from tempo_legacy_database.adapters.authored_repertoires.integrity.certificate import integrity_certificate_from_row
from .recombination import _source_header
from .game_feedback_authority import (CARD_COLUMNS, GRAPH_COLUMNS, CurrentOwnerDecisions,
    _require, _rows)


def read_current_feedback_target(database, target):
    """Read one exact Study target; never enumerate the repertoire catalogue."""
    header = _source_header(database, target.repertoire_id)
    certificate = (integrity_certificate_from_row(dict(header),target.repertoire_id,
        header['graph_generation']) if header and header['graph_state'] == 'ready' else None)
    _require(certificate is not None, 'current_owner_integrity_unavailable')
    card = database.execute_native('SELECT '+CARD_COLUMNS+' FROM cards WHERE id=%s',
        (target.presentation_id,)).fetchone()
    memberships = database.execute_native('''SELECT repertoire_id,card_id,canonical_route_source
        FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id LIMIT 101''',
        (target.presentation_id,)).fetchall()
    graphs = database.execute_native('SELECT '+GRAPH_COLUMNS+''' FROM opening_graph_steps
        WHERE repertoire_id=%s AND generation=%s AND card_id=%s
        ORDER BY line_id,decision_index LIMIT 257''',
        (target.repertoire_id,header['graph_generation'],target.presentation_id)).fetchall()
    _require(card is not None and len(memberships) <= 100 and len(graphs) <= 256,
        'current_owner_target_missing_or_exceeds_bound')
    return dict(repertoire_id=target.repertoire_id, header=dict(header), certificate=certificate, card=dict(card),
        memberships=[dict(row) for row in memberships], graphs=[dict(row) for row in graphs])


class CurrentQualifiedOwnerDecisions(CurrentOwnerDecisions):
    """Current path with its own explicit constructor and target capability.

    Shared selection/publication/lineage fences are inherited from Study's
    existing authority. No LegacyAuthorityToken or catalogue proof is constructed.
    The legacy constructor and compatibility entry points remain unchanged.
    """
    def __init__(self, *, game, target_facts):
        self.database = None
        self.game = CanonicalGame.model_validate_json(game.model_dump_json())
        self._target = json.loads(json.dumps(target_facts))
        self._prepared_encounter = None
        self._qualified = None
        self._qualified_captures = ()
        self._qualified_snapshots = ()
        self._qualified_candidate_captures = ()
        self._qualification_fence = None
        self._prepared_targets = ()

    @property
    def snapshot(self):
        """Complete selected-target public snapshot, retained for compatibility."""
        repertoire_id = self._target.get('repertoire_id',self._target['header'].get('id'))
        selected = [value for value in self._qualified_snapshots
            if repertoire_id in value.membership.scope_repertoire_ids]
        _require(len(selected) == 1, 'first_departure_selected_scope_unproved')
        return selected[0]

    def _validate_qualified_game(self, game, command):
        source = command.source if hasattr(command,'source') else command.finding
        provider = 'chesscom' if game.source_id == 'chess.com' else game.source_id
        _require(game == self.game and (game.workspace_id,provider,game.game_id,
            game.source_revision,game.source_digest) == (command.key.workspace_id,
            command.key.provider,command.key.game_id,source.source_revision,source.source_digest)
            and game.played_at is not None and command.occurred_at is not None
            and instant(game.played_at) == instant(command.occurred_at),
            'first_departure_canonical_source_unproved')

    def introduction_revision(self, repertoire_id):
        headers = [header for header in self._qualified_captures if header['id'] == repertoire_id]
        _require(len(headers) == 1, 'first_departure_selected_scope_unproved')
        return headers[0]['scope_source_revision'] + 1

    def prepare_encounter(self, command):
        """Prove interior executable identity outside the short writer."""
        actual = self._target['card']
        _require(actual['id'] == command.decision.presentation_id
            and actual['revision'] == command.decision.presentation_revision
            and actual['trained_color'] == self.game.learner_color,
            'current_owner_card_changed')
        moves = json.loads(actual['moves_json'])
        _require(1 <= len(moves) <= 40, 'current_owner_executable_exceeds_bound')
        board, matching = chess.Board(actual['start_fen']), []
        _require(board.is_valid(), 'current_owner_illegal_executable')
        for offset, uci in enumerate(moves):
            _require(chess.Move.from_uci(uci) in board.legal_moves, 'current_owner_illegal_executable')
            if (decision_identity(actual['id'],offset,moves[:offset]) == command.key.decision_id
                    and board.turn == (actual['trained_color'] == 'white')
                    and canonical_fen_key(board.fen(en_passant='legal')) == command.decision.position_key
                    and command.expected_uci == uci):
                matching.append(offset)
            board.push_uci(uci)
        _require(len(matching) == 1, 'current_owner_decision_not_in_executable')
        self._prepared_encounter = command.model_dump_json()
        return self

    def _mapping_for_target(self, command, actual, graphs):
        target = command.decision
        mappings = tuple(item for item in self.snapshot.decision_presentations
                         if item.decision_id == command.key.decision_id)
        _require(mappings and len({item.model_dump_json(exclude={'route_id', 'route_ply'})
                                   for item in mappings}) == 1,
                 'current_owner_conflicting_decision_bindings')
        if len(mappings) > 1 or len(graphs) > 1:
            routes = {item.route_id for item in mappings}
            _require(None not in routes and routes == {row['line_id'] for row in graphs},
                     'current_owner_incomplete_decision_bindings')
        supplied = tuple(item for item in mappings
                         if (item.route_id, item.route_ply) == (target.route_id, target.route_ply))
        _require(supplied, 'current_owner_supplied_route_unproved')
        mapping = supplied[0]
        _require(mapping.presentation_revision == actual['revision'], 'current_owner_card_revision_changed')
        return mapping

    def _current_prepared_qualified(self, command):
        """Exact locked target/currentness checks; no chess, capture or provider."""
        database, expected = self.database, self._target
        _require(not database.read_only and database.in_transaction,
            'borrowed_owner_writer_transaction_required')
        _require(self._prepared_encounter == command.model_dump_json(),
            'first_departure_prepared_authority_required')
        target = command.decision
        header = _source_header(database,target.repertoire_id,fence=True)
        certificate = (integrity_certificate_from_row(dict(header),target.repertoire_id,
            header['graph_generation']) if header and header['graph_state'] == 'ready' else None)
        _require(certificate is not None and certificate == expected['certificate']
            and all(header[key] == expected['header'][key] for key in
                ('scope_source_revision','canonical_prefix_revision','graph_generation','graph_state')),
            'current_owner_integrity_changed')
        _require(database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
            ('tempo:card-edit:'+target.presentation_id,)).fetchone()[0], 'current_owner_card_busy')
        actual = database.execute_native('SELECT '+CARD_COLUMNS+' FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED',
            (target.presentation_id,)).fetchone()
        compared = ','.join(field for field in CARD_COLUMNS.split(',') if field != 'introduced_at')
        _require(actual is not None and _rows([actual],compared) == _rows([expected['card']],compared),
            'current_owner_card_changed')
        memberships = database.execute_native('''SELECT repertoire_id,card_id,canonical_route_source
            FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id LIMIT 101 FOR SHARE SKIP LOCKED''',
            (target.presentation_id,)).fetchall()
        _require(len(memberships) <= 100 and _rows(memberships,'repertoire_id,card_id,canonical_route_source')
            == _rows(expected['memberships'],'repertoire_id,card_id,canonical_route_source')
            and any(row['repertoire_id'] == target.repertoire_id for row in memberships),
            'current_owner_membership_changed')
        graphs = database.execute_native('SELECT '+GRAPH_COLUMNS+''' FROM opening_graph_steps
            WHERE repertoire_id=%s AND generation=%s AND card_id=%s
            ORDER BY line_id,decision_index LIMIT 257 FOR SHARE SKIP LOCKED''',
            (target.repertoire_id,header['graph_generation'],target.presentation_id)).fetchall()
        _require(graphs and len(graphs) <= 256 and _rows(graphs,GRAPH_COLUMNS)
            == _rows(expected['graphs'],GRAPH_COLUMNS)
            and any(row['line_id'] == target.route_id for row in graphs), 'current_owner_graph_binding_changed')
        blocked = database.execute_native('''SELECT 1 FROM repertoire_integrity_card_blocks
            WHERE repertoire_id=%s AND card_id=%s UNION ALL SELECT 1 FROM deleted_cards WHERE card_id=%s LIMIT 1''',
            (target.repertoire_id,target.presentation_id,target.presentation_id)).fetchone()
        _require(not blocked and actual['content_type'] == 'opening' and not any(actual[field]
            for field in ('archived','pending_validation','superseded_by')),
            'current_owner_card_unavailable_or_changed_content_introduction_unproved')
        mapping = self._mapping_for_target(command, actual, graphs)
        introduced = actual['introduced_at']
        return (mapping.model_copy(update=dict(introduction='introduced' if introduced is not None else 'unintroduced',
            introduced_at=introduced if introduced and len(introduced)>10 else None,
            introduced_before_game=None,introduction_game=None)),)
