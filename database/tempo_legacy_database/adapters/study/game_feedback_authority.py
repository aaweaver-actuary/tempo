"""Current-owner fencing for T06; no producer call or new certificate authority.

Composition passes the existing backend-owned retained capture and its public
RepertoireSnapshot projection, never a caller-supplied producer snapshot alone.
Every use rereads/fences actual source rows in the same borrowed transaction.
"""
from __future__ import annotations

import json
import copy
from datetime import date

import chess
from tempo_chess_core import canonical_fen_key
from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken, decision_identity, fingerprint
from tempo_game_evidence_contracts.legacy_scope import LegacyIntegrityCertificate, LegacyScopedCatalogueProof

from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.study.game_feedback import GameFeedback, game_scoped_introduction, instant
from app.study.encounter_feedback import EncounterAuthorityUnavailable
from .game_feedback import PostgresGameFeedbackStore

CARD_COLUMNS = "id,revision,start_fen,moves_json,trained_color,content_type,archived,pending_validation,superseded_by,repertoire_id,canonical_route_source,introduced_at"
GRAPH_COLUMNS = "repertoire_id,generation,line_id,decision_index,card_id,starting_fen,moves_json,trained_color"
LINE_COLUMNS = "id,repertoire_id,trained_color,start_fen,moves_json"


class CurrentDecisionUnavailable(EncounterAuthorityUnavailable):
    """Truthful retry/hold reason; caller rolls back this command transaction."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _require(condition, reason):
    if not condition:
        raise CurrentDecisionUnavailable(reason)


def _rows(rows, columns):
    fields = columns.split(',')
    return sorted((tuple(row[field] for field in fields) for row in rows), key=repr)


class CurrentOwnerDecisions:
    def __init__(self, database, *, retained_owner_capture, snapshot: RepertoireSnapshot):
        self.database = database
        self.source = json.loads(json.dumps(retained_owner_capture))
        source = self.source
        _require(source.get('kind') == 'legacy_game_capture' and source.get('schema_version') == 1,
                 'retained_owner_capture_required')
        _require(source.get('export_digest') == fingerprint({k:v for k,v in source.items() if k != 'export_digest'}),
                 'retained_owner_capture_changed')
        self.authority = LegacyAuthorityToken.model_validate(source['authority'])
        self.proof = LegacyScopedCatalogueProof.model_validate(source['catalogue_scope'])
        self.snapshot = RepertoireSnapshot.model_validate_json(snapshot.model_dump_json())
        self._prepared_encounter = None
        _require(source['authority'] == source['observed_after']
                 and snapshot.membership.digest == source['export_digest']
                 and snapshot.membership.source_revision == fingerprint(source['authority'])
                 and snapshot.membership.revision == self.authority.graph_generation,
                 'retained_owner_projection_mismatch')
        _require(self.proof.authority_id == self.authority.storage_identity()[0]
                 and self.proof.catalogue_generation == self.authority.catalogue_generation
                 and self.proof.source_identity_digests() == {k:source['authority'][k] for k in
                    ('source_digest','graph_digest','catalogue_digest')}, 'retained_owner_scope_mismatch')
        _require(len(source['tables']['repertoire_lines']) <= 64 and len(source['graphs']) <= 256
                 and len(source['tables']['cards']) <= 128, 'retained_owner_scope_exceeds_bound')

    def bind(self, database):
        """Reuse already validated retained facts; current database fences still run."""
        bound = copy.copy(self)
        bound.database = database
        return bound

    def prepare_encounter(self, command):
        """Validate retained executable before binding a writer; current rows remain fenced."""
        cards = [row for row in self.source['tables']['cards'] if row['id'] == command.decision.presentation_id]
        _require(len(cards) == 1, 'current_owner_card_changed')
        self._validate_executable(command, cards[0], priority_only=True)
        self._prepared_encounter = command.model_dump_json()
        return self

    def _validate_executable(self, command, actual, *, priority_only):
        key, proof, source = command.key, self.proof, self.source
        finding = command.decision if priority_only else command.finding
        expected_uci = (command.expected_uci,) if priority_only else finding.expected_uci
        card_id = finding.presentation_id
        moves = json.loads(actual['moves_json'])
        _require(1 <= len(moves) <= 40, 'current_owner_executable_exceeds_bound')
        board, matching = chess.Board(actual['start_fen']), []
        _require(actual['trained_color'] == proof.scope.learner_color == source['game']['color'],
                 'current_owner_learner_color_changed')
        for offset, uci in enumerate(moves):
            _require(chess.Move.from_uci(uci) in board.legal_moves, 'current_owner_illegal_executable')
            if (decision_identity(card_id,offset,moves[:offset]) == key.decision_id
                    and board.turn == (actual['trained_color'] == 'white')
                    and canonical_fen_key(board.fen(en_passant='legal')) == finding.position_key
                    and expected_uci == (uci,)):
                matching.append(offset)
            board.push_uci(uci)
        _require(len(matching) == 1, 'current_owner_decision_not_in_executable')

    def current(self, lapse):
        return self._current(lapse)

    def current_encounter(self, encounter):
        return self._current(encounter, priority_only=True)

    def _current(self, command, *, priority_only=False):
        key = command.key
        finding = command.decision if priority_only else command.finding
        expected_uci = (command.expected_uci,) if priority_only else finding.expected_uci
        played_uci = command.played_uci if priority_only else finding.played_uci
        database, source, token, proof = self.database, self.source, self.authority, self.proof
        _require(not database.read_only and database.raw.info.transaction_status.name == 'INTRANS',
                 'borrowed_owner_writer_transaction_required')
        retained_provider = {'lichess':'lichess', 'chess.com':'chesscom', 'chesscom':'chesscom'}.get(
            source['game']['provider'])
        _require((key.workspace_id,finding.repertoire_id,key.game_id,key.provider)
                 == (token.workspace_id,token.repertoire_id,source['game']['id'],retained_provider),
                 'retained_owner_command_scope_mismatch')
        ply = key.occurrence_ply - 1
        _require(ply in proof.scope.decision_plies and proof.scope.game_id == key.game_id
                 and proof.prefix_policies[ply] == [token.repertoire_id]
                 and instant(proof.scope.occurred_at) == instant(source['game']['played_at'])
                 and command.occurred_at is not None
                 and instant(command.occurred_at) == instant(proof.scope.occurred_at)
                 and proof.scope.moves == json.loads(source['game']['moves_json'])
                 and proof.scope.moves[ply] == played_uci,
                 'retained_owner_occurrence_unproved')
        # Exact card locks belong to intake; refresh additionally owns the day. Never wait backwards
        # for graph/source locks; busy/missing authority grants no mutation.
        held = database.execute_native(
            "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
            ('tempo:opening-graph:' + token.repertoire_id,)).fetchone()[0]
        _require(held, 'current_owner_graph_busy')
        header = database.execute_native("""SELECT repertoire.scope_source_revision,
            repertoire.canonical_prefix_revision,scope.generation catalogue_generation,
            publication.generation graph_generation,publication.state graph_state,
            identity.target_id,identity.workspace_id,
            integrity.status integrity_status,integrity.scan_status,integrity.scan_generation,
            integrity.checked_at,integrity.scan_completed_sources,integrity.scan_total_sources,integrity.scan_error,
            graph.generation graph_task_generation,graph.state graph_task_state,
            scan.id scan_task_id,scan.generation scan_task_generation,scan.state scan_task_state,scan.payload_json
            FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope
            CROSS JOIN legacy_learning_identity identity
            JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
            JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
            JOIN background_tasks graph ON graph.kind='opening_graph_rebuild' AND graph.deduplication_key=repertoire.id
            JOIN background_tasks scan ON scan.kind='integrity_scan' AND scan.deduplication_key=repertoire.id
            WHERE repertoire.id=%s AND scope.id=1 AND identity.id=1
            FOR SHARE OF repertoire,scope,identity,publication,integrity,graph,scan SKIP LOCKED""",
            (token.repertoire_id,)).fetchone()
        _require(header is not None, 'current_owner_authority_busy_or_missing')
        fields = ('scope_source_revision','canonical_prefix_revision','catalogue_generation',
                  'graph_generation','graph_state','target_id','workspace_id')
        _require(all(header[k] == getattr(token,k) for k in fields), 'current_owner_revision_changed')
        certificate = {k:header[k] for k in LegacyIntegrityCertificate.model_fields}
        try:
            current_certificate = LegacyIntegrityCertificate.model_validate(certificate)
        except ValueError as error:
            raise CurrentDecisionUnavailable('current_owner_integrity_unavailable') from error
        payload = json.loads(header['payload_json'])
        _require(current_certificate == proof.integrity_certificate
                 and current_certificate.graph_task_generation == token.graph_generation
                 and payload.get('repertoire_id') == token.repertoire_id
                 and payload.get('graph_generation') == token.graph_generation,
                 'current_owner_integrity_changed')
        # Source scope generation fences inserted/deleted candidate routes; exact
        # retained route/card/graph/membership rows are additionally rechecked.
        lines = source['tables']['repertoire_lines']
        identifiers = [row['id'] for row in lines]
        _require(identifiers, 'current_owner_routes_missing')
        actual_lines = database.execute_native('SELECT ' + LINE_COLUMNS +
            ' FROM repertoire_lines WHERE id=ANY(%s) ORDER BY id FOR SHARE SKIP LOCKED', (identifiers,)).fetchall()
        _require(_rows(actual_lines,LINE_COLUMNS) == _rows(lines,LINE_COLUMNS), 'current_owner_routes_changed')
        card_id = finding.presentation_id
        actual = database.execute_native('SELECT ' + CARD_COLUMNS +
            ' FROM cards WHERE id=%s FOR SHARE SKIP LOCKED', (card_id,)).fetchone()
        captured = [row for row in source['tables']['cards'] if row['id'] == card_id]
        # Introduction can legitimately race priority delivery. Only that field
        # is omitted for an encounter; definition/revision/membership stay exact.
        compared = ','.join(field for field in CARD_COLUMNS.split(',')
                            if not priority_only or field != 'introduced_at')
        _require(actual is not None and len(captured) == 1
                 and _rows([actual],compared) == _rows(captured,compared), 'current_owner_card_changed')
        memberships = database.execute_native("""SELECT repertoire_id,card_id,canonical_route_source
            FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id LIMIT 101 FOR SHARE SKIP LOCKED""",
            (card_id,)).fetchall()
        expected_memberships = [row for row in source['tables']['repertoire_cards'] if row['card_id'] == card_id]
        _require(len(memberships) <= 100 and _rows(memberships,'repertoire_id,card_id,canonical_route_source')
                 == _rows(expected_memberships,'repertoire_id,card_id,canonical_route_source')
                 and any(row['repertoire_id'] == token.repertoire_id for row in memberships),
                 'current_owner_membership_changed')
        graphs = database.execute_native('SELECT ' + GRAPH_COLUMNS + """ FROM opening_graph_steps
            WHERE repertoire_id=%s AND generation=%s AND card_id=%s
            ORDER BY line_id,decision_index LIMIT 257 FOR SHARE SKIP LOCKED""",
            (token.repertoire_id,token.graph_generation,card_id)).fetchall()
        expected_graphs = [row for row in source['graphs'] if row['card_id'] == card_id]
        _require(graphs and len(graphs) <= 256 and _rows(graphs,GRAPH_COLUMNS) == _rows(expected_graphs,GRAPH_COLUMNS),
                 'current_owner_graph_binding_changed')
        blocked = database.execute_native("""SELECT 1 FROM repertoire_integrity_card_blocks
            WHERE repertoire_id=%s AND card_id=%s UNION ALL SELECT 1 FROM deleted_cards WHERE card_id=%s LIMIT 1""",
            (token.repertoire_id,card_id,card_id)).fetchone()
        _require(not blocked and (priority_only or actual['revision'] == 1) and not any(actual[k] for k in
                 ('archived','pending_validation','superseded_by')) and actual['content_type'] == 'opening',
                 'current_owner_card_unavailable_or_changed_content_introduction_unproved')
        if not priority_only or self._prepared_encounter != command.model_dump_json():
            self._validate_executable(command, actual, priority_only=priority_only)
        mappings = tuple(item for item in self.snapshot.decision_presentations
            if item.decision_id == key.decision_id)
        # Route provenance is not a second physical target. Prove every other
        # fact equal, retaining the complete snapshot and current graph binding.
        _require(mappings and len({item.model_dump_json(exclude={'route_id','route_ply'})
                                   for item in mappings}) == 1,
                 'current_owner_conflicting_decision_bindings')
        if len(mappings) > 1 or len(graphs) > 1:
            routes = {item.route_id for item in mappings}
            _require(None not in routes and routes == {row['line_id'] for row in graphs},
                     'current_owner_incomplete_decision_bindings')
        if priority_only:
            supplied = tuple(item for item in mappings
                if (item.route_id,item.route_ply) == (finding.route_id,finding.route_ply))
            _require(supplied, 'current_owner_supplied_route_unproved')
            mapping = supplied[0]  # Already proved equivalent; keep the actual supplied member.
        else:
            mapping = min(mappings, key=lambda item:(item.route_id or '',
                -1 if item.route_ply is None else item.route_ply))
        if priority_only:
            _require(mapping.presentation_revision == actual['revision'], 'current_owner_card_revision_changed')
            # Priority makes no chronology assertion. The original command stays
            # immutable; current introduction determines fresh admission only.
            introduced = actual['introduced_at']
            return (mapping.model_copy(update=dict(
                introduction='introduced' if introduced is not None else 'unintroduced',
                introduced_at=introduced if introduced and len(introduced) > 10 else None,
                introduced_before_game=None,introduction_game=None)),)
        if mapping.introduced_at is None:
            _require(game_scoped_introduction(mapping,command), 'current_owner_introduction_game_scope_mismatch')
            recorded=actual['introduced_at']
            try:
                before=(isinstance(recorded,str) and len(recorded)==10
                        and date.fromisoformat(recorded)<instant(command.occurred_at).date())
            except ValueError:
                before=False
            _require(before and captured[0].get('introduced_before_game') is True
                     and mapping.introduction=='introduced' and mapping.introduction_matches_current_content,
                     'current_owner_day_only_introduction_unproved')
        else:
            _require(mapping.introduced_at == actual['introduced_at'],
                     'current_owner_exact_introduction_time_unproved')
        return (mapping,)


def current_owner_game_feedback(database, *, retained_owner_capture, snapshot, clock,
                                light_first_interval_days=7):
    """T07 factory: use inside its existing writer transaction, then commit once.

    Caller authenticates/fences the canonical imported game in its own boundary;
    this adapter fences repertoire/Study state and never reads/calls Games.
    """
    return GameFeedback(PostgresGameFeedbackStore(database),
        CurrentOwnerDecisions(database, retained_owner_capture=retained_owner_capture, snapshot=snapshot),
        clock=clock, light_first_interval_days=light_first_interval_days)


def current_owner_encounter_intake(database, *, retained_owner_capture, snapshot, clock):
    """Internal intake factory. No queue policy or mutation capability."""
    from app.study.encounter_feedback import EncounterFeedback
    return EncounterFeedback(PostgresGameFeedbackStore(database),
        CurrentOwnerDecisions(database,retained_owner_capture=retained_owner_capture,snapshot=snapshot),
        clock=clock,introductions=None,introduction_policy=None)


def current_owner_encounter_feedback(database, *, retained_owner_capture, snapshot, clock,
                                     introductions, introduction_policy, prepared_priority=None, prepared_decisions=None):
    """Internal Study refresh factory on its own borrowed writer transaction.

    Compose #495's current_owner_encounter_introductions externally. No new
    schema, connection, commit, live policy default or producer lifecycle.
    """
    from app.study.encounter_feedback import EncounterFeedback
    decisions = (prepared_decisions.bind(database) if prepared_decisions is not None else
                 CurrentOwnerDecisions(database, retained_owner_capture=retained_owner_capture, snapshot=snapshot))

    class RetainedIntroductionTarget:
        def admit_encounter(self, day, *, repertoire_id, decision_id, target):
            # Retained snapshots label mappings with graph generation; #495's
            # exact current-target API uses source revision + 1. Both are actual
            # facts checked under the same source fence above, not a guessed
            # revision or new physical mapping. Preserve every decision field.
            current = target.model_copy(update={
                'repertoire_revision': decisions.authority.scope_source_revision + 1})
            return introductions.admit_encounter(day,repertoire_id=repertoire_id,
                                                decision_id=decision_id,target=current)

    return EncounterFeedback(PostgresGameFeedbackStore(database, prepared_priority=prepared_priority), decisions,
        clock=clock,introductions=RetainedIntroductionTarget() if introductions is not None else None,
        introduction_policy=introduction_policy)
