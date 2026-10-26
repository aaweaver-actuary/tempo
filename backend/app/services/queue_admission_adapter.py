"""Legacy queue admission storage, borrowing the caller's connection.

Fixed queries, parameter expansion and dialect mechanics live in root database
storage. This composition retains admission/page decisions, callbacks and clocks;
no connection, transaction or writer is created.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import frozen_opening_order
from .real_game_feedback import MISS_REASON
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_query
from .real_game_feedback_adapter import has_outstanding_real_game_miss

from .queue_admission import IntroductionCountPage, OpeningCandidatePage

READ_BATCH_SIZE = 16


from tempo_legacy_database.adapters.queue_refresh.application import admission as admission_storage
from tempo_legacy_database.adapters.queue_refresh.application.admission import _PRIORITY_OPENING_CANDIDATE_BODY


class QueueAdmissionAdapter:
    miss_reason = MISS_REASON

    def __init__(self, database):
        self.database = database

    def frozen_active(self):
        return frozen_opening_order.activation(self.database)[0] is not None

    def unseen_openings(self, day):
        result, after = [], None
        while True:
            # Capture all facts before reconciliation changes their eligibility.
            rows = admission_storage.unseen_opening_page(self.database, day, after, READ_BATCH_SIZE)
            if not rows:
                return result
            result.extend(dict(row) for row in rows)
            after = (rows[-1]['position'], rows[-1]['id'])

    def introduction_count_page(self, day, after_repertoire_id, *, reviewed_only=False):
        # Historical introductions consume allowance even if integrity or
        # membership eligibility changes. Requeues never charge a second time.
        rows = admission_storage.introduction_count_rows(
            self.database, day, after_repertoire_id, reviewed_only, READ_BATCH_SIZE,
        )
        return IntroductionCountPage(tuple((row[0], int(row[1])) for row in rows),
                                     rows[-1][0] if len(rows) == READ_BATCH_SIZE else None)

    def has_outstanding_miss(self, card_id):
        return has_outstanding_real_game_miss(self.database, card_id)

    def retain_unseen(self, day, card_id):
        admission_storage.retain_unseen(self.database, day, card_id)

    def remove_unseen(self, entry_id, card_id):
        admission_storage.remove_unseen(self.database, entry_id, card_id)

    def _priority_page(self, day, card_ids, *, capacity=False):
        if not card_ids:
            return []
        if len(card_ids) > READ_BATCH_SIZE:
            raise ValueError('Opening candidate page exceeds the adapter bound')
        rows = admission_storage.priority_rows(
            self.database, day, card_ids, MISS_REASON, capacity=capacity,
        )
        return [dict(row) for row in rows]

    def opening_candidate_page(self, day, after_card_id):
        identifiers = [row[0] for row in admission_storage.candidate_identifiers(self.database, after_card_id, READ_BATCH_SIZE)]
        return OpeningCandidatePage(tuple(self._priority_page(day, identifiers)) if identifiers else (),
                                    identifiers[-1] if len(identifiers) == READ_BATCH_SIZE else None)

    def annotate_openings(self, candidates):
        return frozen_opening_order.annotate(self.database, candidates)

    def opening_limits(self, default):
        if isinstance(default, dict):
            return default
        result, after = {}, ''
        while True:
            rows = admission_storage.repertoire_identifiers(self.database, after, READ_BATCH_SIZE)
            if not rows:
                return result
            result.update((row[0], default) for row in rows)
            after = rows[-1][0]

    def capacity_enabled(self):
        from .opening_capacity_admission import capacity_expansion_enabled
        return capacity_expansion_enabled()

    def study_only(self):
        from .study_mode import study_only_enabled
        return study_only_enabled(self.database)

    def capacity_routes(self, repertoire_id, parent_ids):
        from .opening_capacity_admission import CAPACITY_READ_BATCH_SIZE, capacity_routes_from_rows
        return capacity_routes_from_rows(admission_storage.borrowed_capacity_route_rows(
            self.database, repertoire_id, parent_ids, CAPACITY_READ_BATCH_SIZE,
        ))

    def capacity_candidates(self, day, card_ids):
        result = []
        for offset in range(0, len(card_ids), READ_BATCH_SIZE):
            result.extend(self._priority_page(day, card_ids[offset:offset+READ_BATCH_SIZE], capacity=True))
        return result

    def frozen_proposal_valid(self, proposal):
        # This is the existing frozen-activation fence, not general card/route
        # eligibility. It returns true when the inactive token still matches.
        return frozen_opening_order.proposal_valid(self.database, proposal)

    def capacity_route_valid(self, proposal, day):
        from .opening_capacity_admission import capacity_route_still_valid
        return capacity_route_still_valid(self.database, proposal, day)

    def introduced_count(self, day, repertoire_id):
        return int(admission_storage.introduced_count(self.database, day, repertoire_id))

    def current_limit(self, repertoire_id):
        row = admission_storage.current_limit(self.database, repertoire_id)
        return int(row[0]) if row else None

    def introduce_opening(self, day, proposal, position):
        admission_storage.append_opening(self.database, day, proposal['card_id'], position, proposal['reason'], proposal['repertoire_id'])
        self.retain_unseen(day, proposal['card_id'])
        admission_storage.resolve_opportunity(
            self.database, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
            proposal['repertoire_id'], proposal['card_id'],
        )


class CurrentIntroductionUnavailable(RuntimeError):
    pass


def _playable_introduction(mapping, card, graphs, routes, *, learner_color):
    """Recheck exact physical definition inside an explicitly published route."""
    import json
    import chess
    from tempo_evidence_math import opening_position_key
    from tempo_game_evidence_contracts.legacy import decision_identity
    if (not mapping.available or mapping.presentation_revision != card['revision']
            or card['trained_color'] != learner_color or card['content_type'] != 'opening'
            or any(card[key] for key in ('archived', 'pending_validation', 'superseded_by', 'blocked', 'deleted'))
            or not card['member']):
        return False
    try:
        moves = json.loads(card['moves_json'])
        if not 1 <= len(moves) <= 40:
            return False
        board = chess.Board(card['start_fen'])
        matching = []
        for offset, uci in enumerate(moves):
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                return False
            if (board.turn == (learner_color == 'white') and mapping.selected_response_uci == uci
                    and mapping.position_key == opening_position_key(board.fen())
                    and mapping.decision_id == decision_identity(card['id'], offset, moves[:offset])):
                matching.append(offset)
            board.push(move)
        if not matching or board.turn == (learner_color == 'white'):
            return False
        for graph in graphs:
            route = routes.get(graph['line_id'])
            if (graph['card_id'] != card['id'] or route is None or graph['trained_color'] != learner_color
                    or json.loads(graph['moves_json']) != moves
                    or opening_position_key(graph['starting_fen']) != opening_position_key(card['start_fen'])):
                continue
            if getattr(mapping, 'route_id', None) not in (None, graph['line_id']):
                continue
            route_board = chess.Board(route['start_fen'])
            route_moves = json.loads(route['moves_json'])
            if not 1 <= len(route_moves) <= 1000 or route['trained_color'] != learner_color:
                continue
            roots = []
            for ply, uci in enumerate(route_moves):
                if (opening_position_key(route_board.fen()) == opening_position_key(card['start_fen'])
                        and route_moves[ply:ply + len(moves)] == moves):
                    roots.append(ply)
                move = chess.Move.from_uci(uci)
                if move not in route_board.legal_moves:
                    return False
                route_board.push(move)
            if len(roots) == 1 and (getattr(mapping, 'route_ply', None) is None
                    or any(roots[0] + offset == mapping.route_ply for offset in matching)):
                return True
    except (ValueError, TypeError, KeyError):
        return False
    return False


class CurrentStudyIntroductionOwner:
    """Concrete current Study facts/fences; no producer or composition callback."""
    def __init__(self, database, *, sources, settings):
        from app.study.introductions.policy import validated_weights
        self.database, self.sources, self.settings = database, tuple(sources), settings
        names = [source.authority.repertoire_id for source in self.sources]
        if len(names) != len(set(names)) or len(names) > 32:
            raise ValueError('unique bounded introduction sources required')
        if set(validated_weights(settings.weights)) != set(names):
            raise ValueError('explicit weights must bind each supplied repertoire')
        self.legacy = QueueAdmissionAdapter(database)
        self.fencing = False
        self.diagnostics = ()

    def facts(self, day):
        import json
        from tempo_game_evidence_contracts.legacy import fingerprint
        from app.study.introductions.policy import Candidate, RankingContext, RequiredWork
        from app.study.introductions.ports import OwnerFacts
        controls, today, required, queued = admission_storage.product_current_queue_facts(
            self.database, day, fence=self.fencing)
        if controls is None:
            raise CurrentIntroductionUnavailable('current_study_controls_busy_or_missing')
        candidates, bindings, introduced, definitions, diagnostics = [], [], {row[0] for row in today}, {}, []
        for source in self.sources:
            token, snapshot = source.authority, source.snapshot
            mappings = [item for item in snapshot.decision_presentations if item.repertoire_id == token.repertoire_id]
            expected_routes = {route.route_id: route for route in snapshot.membership.routes if route.repertoire_id == token.repertoire_id}
            header, lines, cards, graphs = admission_storage.product_source_facts(
                self.database, token.repertoire_id, sorted({item.presentation_id for item in mappings}),
                tuple(expected_routes), fence=self.fencing)
            fields = ('scope_source_revision', 'canonical_prefix_revision', 'catalogue_generation',
                      'graph_generation', 'graph_state', 'target_id', 'workspace_id')
            valid = header is not None and all(header[key] == getattr(token, key) for key in fields) and token.graph_state == 'ready'
            bindings.append((token.repertoire_id, token.graph_generation,
                             snapshot.membership.digest if valid else 'stale:' + fingerprint(tuple(header) if header else None)))
            if not valid or len(graphs) > 256:
                diagnostics.append((token.repertoire_id, 'current_source_busy_missing_changed_or_oversized'))
                continue
            routes = {}
            for row in lines:
                try:
                    expected = expected_routes[row['id']]
                    if (row['start_fen'] == expected.starting_fen and json.loads(row['moves_json']) == list(expected.moves)
                            and row['trained_color'] == expected.learner_color):
                        routes[row['id']] = row
                except (ValueError, TypeError, KeyError):
                    continue
            by_id = {row['id']: row for row in cards}
            definitions.update(by_id)
            introduced.update(row['id'] for row in cards if row['introduced_at'] is not None)
            for mapping in mappings:
                card = by_id.get(mapping.presentation_id)
                if (card is None or card['introduced_at'] is not None or card['id'] in queued
                        or card['state'] not in ('new', 'locked')
                        or not _playable_introduction(mapping, card, graphs, routes, learner_color=snapshot.learner_color)):
                    continue
                # This is the agreed deep-introduction path: no ancestor review
                # or stability condition. The whole actual route was checked.
                candidates.append(Candidate(mapping.decision_id, card['id'], token.repertoire_id,
                    fingerprint([card['revision'], card['start_fen'], card['moves_json'], card['trained_color']]),
                    len(candidates), approved=True, legal=True, conflicted=False, mapped=True, playable_context=True))
        # Reuse the existing frozen-order/breadth preference as offline fallback.
        from app.study.openings.capacity_frontier import plan_prioritized_opening_admissions
        rows = self.legacy.annotate_openings([dict(id=item.presentation_id, repertoire_id=item.repertoire_id,
            moves_json=definitions[item.presentation_id]['moves_json'], gameplay_priority_reason=None, priority_date=None, priority_score=None,
            completed_line_ids_json=None, frontier_decisions_json=None) for item in candidates])
        ordered = plan_prioritized_opening_admissions(rows, {}, day, len(rows), miss_reason=self.legacy.miss_reason)
        fallback = {(rep, row['id']): index for index, (rep, row) in enumerate(ordered)}
        from dataclasses import replace
        candidates = tuple(replace(item, fallback_order=fallback.get((item.repertoire_id, item.presentation_id), len(rows)))
                           for item in candidates)
        settings = self.settings
        context = RankingContext(tuple(bindings), fingerprint([settings.revision, settings.global_budget,
            settings.weights, settings.enabled, settings.paused, bool(controls[0])]))
        self.diagnostics = tuple(diagnostics)
        return OwnerFacts(context, candidates, settings.global_budget, settings.weights,
            tuple((row[0], row[2]) for row in today), frozenset(introduced),
            tuple(RequiredWork(row[0], 'obligation' if row[1] else 'due') for row in required),
            enabled=settings.enabled, paused=settings.paused, study_only=bool(controls[0]))

    def introduce(self, day, candidate):
        facts = self.facts(day)
        if not facts.enabled or facts.paused or facts.study_only or candidate not in facts.candidates:
            return 'stale'
        from tempo_legacy_database.adapters.queue_refresh.application import seeding
        # Ordinary due work is appended by its existing canonical writer first.
        # Existing issued rows/positions are never changed or reconciled here.
        maximum = seeding.maximum_position(self.database, day)
        for row in seeding.due_cards(self.database, day):
            maximum += 1
            seeding.append_due_card(self.database, day, row[0], maximum)
        self.legacy.introduce_opening(day, dict(card_id=candidate.presentation_id,
            repertoire_id=candidate.repertoire_id, reason=None), maximum + 1)
        return 'introduced'


def current_owner_introductions(database, *, sources, settings):
    """Explicit opt-in factory. No activation, connection or independent commit."""
    from app.study.introductions.application import Introductions
    from tempo_legacy_database.adapters.study.product_introductions import PostgresIntroductionStore

    class CurrentIntroductions(Introductions):
        def commit(self, day, candidate, context):
            if database.read_only or database.raw.info.transaction_status.name != 'INTRANS':
                raise CurrentIntroductionUnavailable('borrowed_writer_transaction_required')
            if not admission_storage.lock_product_admission(database, day, candidate.presentation_id):
                from app.study.introductions.application import CommitResult
                return CommitResult('deferred')
            self.owner.fencing = True
            try:
                return super().commit(day, candidate, context)
            finally:
                self.owner.fencing = False

        def context(self, day):
            return self.owner.facts(day).context

        def admit_next(self, day):
            from app.study.introductions.application import CommitResult
            plan = self.preview(day)
            if not plan.introductions:
                return CommitResult('deferred' if self.owner.diagnostics else 'no_work')
            return self.commit(day, plan.introductions[0], self.context(day))

        def admit(self, day, *, maximum_new=16):
            if not 1 <= maximum_new <= 64:
                raise ValueError('bounded introduction batch required')
            results = []
            for _ in range(maximum_new):
                result = self.admit_next(day)
                if result.status != 'introduced':
                    break
                results.append(result)
            return tuple(results)

    return CurrentIntroductions(PostgresIntroductionStore(database),
        CurrentStudyIntroductionOwner(database, sources=sources, settings=settings))
