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


class CurrentEncounterIntroductions:
    """Exact canonical admission on the caller's existing receipt transaction.

    This capability does not construct the weighted D store. Its input is the
    current shared mapping already resolved by the receiving Study owner; an
    opaque decision hash alone cannot identify a physical presentation safely.
    """
    def __init__(self, database, *, allowance, conflicts=None):
        if allowance != 'ordinary':
            raise ValueError('explicit ordinary encounter allowance required')
        self.database, self.conflicts = database, conflicts

    def _routes(self, repertoire_id):
        import json
        from app.contracts.product_v02.repertoire import ImmutableAuthoredRoute
        after = ''
        while True:
            rows = admission_storage.product_target_route_page(self.database, repertoire_id, after)
            if not rows:
                return
            for row in rows:
                if row['oversized'] or not row['id']:
                    raise ValueError('target route exceeds source row bound')
                yield ImmutableAuthoredRoute(repertoire_id=repertoire_id, route_id=row['id'],
                    policy_id=repertoire_id, learner_color=row['trained_color'],
                    starting_fen=row['start_fen'], moves=json.loads(row['moves_json']))
            after = rows[-1]['id']

    def admit_encounter(self, day, *, repertoire_id, decision_id, target=None):
        from app.contracts.product_v02.repertoire import DecisionPresentation
        from app.study.introductions.application import (CommitResult, Introductions,
            introduction_is_held, ordinary_encounter_allowance, target_alternatives)
        from tempo_legacy_database.adapters.queue_refresh.application import seeding
        from tempo_game_evidence_contracts.legacy import decision_identity
        from tempo_evidence_math import opening_position_key
        import chess
        import json
        Introductions._day(day)
        if self.database.read_only or self.database.raw.info.transaction_status.name != 'INTRANS':
            raise CurrentIntroductionUnavailable('borrowed_writer_transaction_required')
        if (not isinstance(target, DecisionPresentation) or not target.available
                or (target.repertoire_id, target.decision_id) != (repertoire_id, decision_id)):
            return CommitResult('deferred', reason='current_mapping_required')
        if not admission_storage.lock_product_admission(self.database, day, target.presentation_id):
            return CommitResult('deferred', reason='current_target_busy')
        header, _, cards, graphs = admission_storage.product_source_facts(
            self.database, repertoire_id, (target.presentation_id,), (), fence=True)
        if (header is None or header['graph_state'] != 'ready' or len(cards) != 1
                or header['scope_source_revision'] + 1 != target.repertoire_revision or len(graphs) > 256):
            return CommitResult('deferred', reason='current_source_unproved')
        if header['canonical_prefix_moves_json'] != '[]':
            return CommitResult('deferred', reason='canonical_prefix_snapshot_unsupported')
        card = cards[0]
        if (card['revision'] != target.presentation_revision
                or any(card[key] for key in ('archived', 'pending_validation', 'superseded_by', 'blocked', 'deleted'))
                or not card['linked_member'] or card['content_type'] != 'opening'
                or introduction_is_held(self.conflicts, repertoire_id=repertoire_id,
                    presentation_id=card['id'], presentation_revision=card['revision'])):
            return CommitResult('deferred', reason='current_target_unavailable')
        if card['introduced_at'] is not None:
            return CommitResult('already_introduced')
        controls, today, _, queued = admission_storage.product_current_queue_facts(self.database, day, fence=True)
        if controls is None:
            return CommitResult('deferred', reason='current_controls_unproved')
        reason = ordinary_encounter_allowance(study_only=bool(controls[0]), global_limit=controls[1],
            repertoire_limit=header['legacy_repertoire_limit'],
            introduced_repertoires=(row[2] for row in today), repertoire_id=repertoire_id)
        if reason:
            return CommitResult('deferred', reason=reason)
        if card['state'] not in ('new', 'locked') or card['id'] in queued:
            return CommitResult('deferred', reason='current_target_queued_or_started')
        # Introducing a physical presentation exposes every owned move in it.
        # Check complete alternatives for all of them, not only the encounter.
        try:
            moves = json.loads(card['moves_json'])
            board, expected, exact = chess.Board(card['start_fen']), {}, False
            if not board.is_valid() or not 1 <= len(moves) <= 40:
                raise ValueError('unproved physical definition')
            for offset, uci in enumerate(moves):
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError('illegal physical definition')
                if board.turn == (card['trained_color'] == 'white'):
                    position = opening_position_key(board.fen())
                    if position in expected and expected[position] != uci:
                        raise ValueError('conflicting physical definition')
                    expected[position] = uci
                    exact |= (decision_id == decision_identity(card['id'], offset, moves[:offset])
                        and target.position_key == position and target.selected_response_uci == uci)
                board.push(move)
            if not exact:
                raise ValueError('unproved decision identity')
        except (ValueError, TypeError, KeyError):
            return CommitResult('deferred', reason='current_target_definition_unproved')
        alternatives = target_alternatives(self._routes(repertoire_id),
            expected=tuple(expected.items()), learner_color=card['trained_color'])
        if alternatives.reason:
            return CommitResult('deferred', reason=alternatives.reason)
        current_header, lines, current_cards, current_graphs = admission_storage.product_source_facts(
            self.database, repertoire_id, (card['id'],),
            tuple(route.route_id for route in alternatives.routes), fence=True)
        routes = {row['id']: row for row in lines}
        if (current_header is None or tuple(current_header) != tuple(header) or len(current_cards) != 1
                or tuple(current_cards[0]) != tuple(card)
                or [tuple(row) for row in current_graphs] != [tuple(row) for row in graphs]
                or len(routes) != len(alternatives.routes)):
            return CommitResult('deferred', reason='current_source_changed_or_busy')
        if any((routes[route.route_id]['start_fen'], json.loads(routes[route.route_id]['moves_json']),
                routes[route.route_id]['trained_color']) != (route.starting_fen, list(route.moves), route.learner_color)
                for route in alternatives.routes):
            return CommitResult('deferred', reason='current_source_changed_or_busy')
        if not _playable_introduction(target, card, graphs, routes,
                learner_color=card['trained_color'], route_cache={}):
            return CommitResult('deferred', reason='current_playable_context_unproved')
        maximum = seeding.maximum_position(self.database, day)
        for due in seeding.due_cards(self.database, day):
            maximum += 1
            seeding.append_due_card(self.database, day, due[0], maximum)
        QueueAdmissionAdapter(self.database).introduce_opening(day,
            dict(card_id=card['id'], repertoire_id=repertoire_id, reason=None), maximum + 1)
        return CommitResult('introduced')


def current_owner_encounter_introductions(database, *, allowance, conflicts=None):
    """No D storage/weights/ranking dependency; caller owns receipt and commit.

    Pass the exact shared current mapping as ``admit_encounter(..., target=...)``.
    Ordinary admission reads saved global and per-repertoire caps, counts actual
    canonical introductions and uses the existing card/day writer reservation.
    """
    return CurrentEncounterIntroductions(database, allowance=allowance, conflicts=conflicts)


def _playable_introduction(mapping, card, graphs, routes, *, learner_color, route_cache):
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
        if not board.is_valid():
            return False
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
            if route['id'] not in route_cache:
                route_board = chess.Board(route['start_fen'])
                route_moves = json.loads(route['moves_json'])
                if (not route_board.is_valid() or not 1 <= len(route_moves) <= 1000
                        or route['trained_color'] != learner_color):
                    return False
                frames = []
                for uci in route_moves:
                    frames.append(opening_position_key(route_board.fen()))
                    move = chess.Move.from_uci(uci)
                    if move not in route_board.legal_moves:
                        return False
                    route_board.push(move)
                route_cache[route['id']] = route_moves, frames
            route_moves, frames = route_cache[route['id']]
            roots = [ply for ply, position in enumerate(frames)
                     if position == opening_position_key(card['start_fen'])
                     and route_moves[ply:ply + len(moves)] == moves]
            if len(roots) == 1 and (getattr(mapping, 'route_ply', None) is None
                    or any(roots[0] + offset == mapping.route_ply for offset in matching)):
                return True
    except (ValueError, TypeError, KeyError):
        return False
    return False


class CurrentStudyIntroductionOwner:
    """Concrete current Study facts/fences; no producer or composition callback."""
    def __init__(self, database, *, sources, settings, current_snapshots=None, conflicts=None):
        from app.study.introductions.policy import validated_weights
        from app.study.introductions.ports import RepertoireIntroductionSource
        self.database, self.sources, self.settings = database, tuple(sources), settings
        self.current_snapshots, self.conflicts = current_snapshots, conflicts
        if any(isinstance(source, RepertoireIntroductionSource) for source in self.sources) and current_snapshots is None:
            raise ValueError('current repertoire snapshot capability required')
        names = [source.repertoire_id if isinstance(source, RepertoireIntroductionSource)
                 else source.authority.repertoire_id for source in self.sources]
        if len(names) != len(set(names)) or len(names) > 32:
            raise ValueError('unique bounded introduction sources required')
        if set(validated_weights(settings.weights)) != set(names):
            raise ValueError('explicit weights must bind each supplied repertoire')
        self.legacy = QueueAdmissionAdapter(database)
        self.fencing = False
        self.diagnostics = ()
        self.validated_generic_sources = frozenset()

    def facts(self, day):
        import json
        from tempo_game_evidence_contracts.legacy import fingerprint
        from app.study.introductions.policy import Candidate, RankingContext, RequiredWork
        from app.study.introductions.ports import OwnerFacts, RepertoireIntroductionSource
        from app.study.introductions.application import introduction_is_held, repertoire_introduction_binding
        controls, today, required, queued = admission_storage.product_current_queue_facts(
            self.database, day, fence=self.fencing)
        diagnostics = []
        if controls is None:
            # New optional admissions defer; ordinary due work may still commit.
            controls = (True, 0)
            diagnostics.append(('settings', 'current_study_controls_busy_or_missing'))
        candidates, bindings, introduced, definitions, paused_repertoires = [], [], {row[0] for row in today}, {}, []
        route_cache, validated_generic = {}, set()
        for source in self.sources:
            generic = isinstance(source, RepertoireIntroductionSource)
            token, snapshot = (None if generic else source.authority), source.snapshot
            repertoire_id = source.repertoire_id if generic else token.repertoire_id
            revision = snapshot.membership.revision if generic else token.graph_generation
            binding = repertoire_introduction_binding(snapshot) if generic else fingerprint(token.model_dump(mode='json'))
            mappings = [item for item in snapshot.decision_presentations if item.repertoire_id == repertoire_id]
            expected_routes = {route.route_id: route for route in snapshot.membership.routes if route.repertoire_id == repertoire_id}
            if len(mappings) > 256 or len(expected_routes) > 64 or len({item.presentation_id for item in mappings}) > 128:
                diagnostics.append((repertoire_id, 'current_source_mapping_bound_exceeded'))
                bindings.append((repertoire_id, revision, 'oversized:' + binding))
                continue
            header, lines, cards, graphs = admission_storage.product_source_facts(
                self.database, repertoire_id, sorted({item.presentation_id for item in mappings}),
                tuple(expected_routes), fence=self.fencing)
            fields = ('scope_source_revision', 'canonical_prefix_revision', 'catalogue_generation',
                      'graph_generation', 'graph_state', 'target_id', 'workspace_id')
            if generic:
                valid = (header is not None and header['workspace_id'] == snapshot.membership.workspace_id
                         and header['scope_source_revision'] + 1 == revision and header['graph_state'] == 'ready')
                if valid:
                    try:
                        current = self.current_snapshots.snapshot(repertoire_id)
                        expected, observed = snapshot.membership, current.membership
                        valid = ((observed.source_revision, observed.revision, observed.digest)
                                 == (expected.source_revision, expected.revision, expected.digest)
                                 and repertoire_introduction_binding(current) == binding)
                    except ValueError:
                        valid = False
            else:
                valid = header is not None and all(header[key] == getattr(token, key) for key in fields) and token.graph_state == 'ready'
            bindings.append((repertoire_id, revision,
                             binding if valid else 'stale:' + fingerprint(tuple(header) if header else None)))
            if not valid or len(graphs) > 256:
                diagnostics.append((repertoire_id, 'current_source_busy_missing_changed_or_oversized'))
                continue
            if generic:
                validated_generic.add(repertoire_id)
            if header['legacy_repertoire_limit'] == 0:
                paused_repertoires.append(repertoire_id)
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
                        or introduction_is_held(self.conflicts, repertoire_id=repertoire_id,
                            presentation_id=card['id'], presentation_revision=card['revision'])
                        or not _playable_introduction(mapping, card, graphs, routes,
                            learner_color=snapshot.learner_color, route_cache=route_cache)):
                    continue
                # This is the agreed deep-introduction path: no ancestor review
                # or stability condition. The whole actual route was checked.
                candidates.append(Candidate(mapping.decision_id, card['id'], repertoire_id,
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
            settings.weights, settings.enabled, settings.paused, bool(controls[0]), controls[1], sorted(paused_repertoires)]))
        self.diagnostics = tuple(diagnostics)
        self.validated_generic_sources = frozenset(validated_generic)
        return OwnerFacts(context, candidates, settings.global_budget, settings.weights,
            tuple((row[0], row[2]) for row in today), frozenset(introduced),
            tuple(RequiredWork(row[0], 'obligation' if row[1] else 'due') for row in required),
            enabled=settings.enabled, paused=settings.paused or controls[1] == 0, study_only=bool(controls[0]))

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
        # The generic reader includes introduction facts in its raw fence.
        # Refresh only this transaction's already-fenced valid sources after our
        # own canonical write; never waive a stale caller/source fence on entry.
        from app.study.introductions.ports import RepertoireIntroductionSource
        from app.study.introductions.application import repertoire_introduction_binding
        refreshed = []
        for source in self.sources:
            if isinstance(source, RepertoireIntroductionSource) and source.repertoire_id in self.validated_generic_sources:
                current = self.current_snapshots.snapshot(source.repertoire_id)
                if repertoire_introduction_binding(current) != repertoire_introduction_binding(source.snapshot):
                    raise CurrentIntroductionUnavailable('source_changed_during_introduction')
                source = RepertoireIntroductionSource(current)
            refreshed.append(source)
        self.sources = tuple(refreshed)
        return 'introduced'


def current_owner_introductions(database, *, sources, settings, current_snapshots=None, conflicts=None):
    """Explicit opt-in factory. No activation, connection or independent commit."""
    from app.study.introductions.application import Introductions
    from tempo_legacy_database.adapters.study.product_introductions import PostgresIntroductionStore

    class CurrentIntroductions(Introductions):
        def commit(self, day, candidate, context, *, priority=None):
            if database.read_only or database.raw.info.transaction_status.name != 'INTRANS':
                raise CurrentIntroductionUnavailable('borrowed_writer_transaction_required')
            if not admission_storage.lock_product_admission(database, day, candidate.presentation_id):
                from app.study.introductions.application import CommitResult
                return CommitResult('deferred')
            self.owner.fencing = True
            try:
                return super().commit(day, candidate, context, priority=priority)
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

        def admit_encounter(self, day, *, repertoire_id, decision_id):
            """Prioritize a current approved unintroduced decision, never a lapse.

            No ranking/provider is required. The existing global allowance,
            positive weights/period quotas and explicit pause controls still
            govern admission. The caller retains deferred delivery/retry intent.
            """
            from app.study.introductions.application import CommitResult
            self._day(day)
            identities = {item.presentation_id for source in self.owner.sources
                          for item in source.snapshot.decision_presentations
                          if item.repertoire_id == repertoire_id and item.decision_id == decision_id}
            if len(identities) != 1:
                return CommitResult('unavailable')
            presentation_id = next(iter(identities))
            previous = self.store.receipt(presentation_id)
            if previous is not None:
                return CommitResult('replayed', previous)
            facts = self.owner.facts(day)
            if presentation_id in facts.introduced:
                return CommitResult('already_introduced')
            priority = (repertoire_id, decision_id)
            plan = self.preview(day, priority=priority)
            if (not plan.introductions or (plan.introductions[0].repertoire_id,
                    plan.introductions[0].decision_id) != priority):
                return CommitResult('deferred')
            return self.commit(day, plan.introductions[0], facts.context, priority=priority)

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
        CurrentStudyIntroductionOwner(database, sources=sources, settings=settings,
                                      current_snapshots=current_snapshots, conflicts=conflicts))
