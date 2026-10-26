"""Offline-safe ranking consumption and atomic Study-owned introduction effects."""
from dataclasses import dataclass
from datetime import date

from app.contracts.product_v02.analysis import RankingOutput
from app.contracts.product_v02.repertoire import ImmutableAuthoredRoute, RepertoireSnapshot
from .policy import Candidate, RankingContext, Selection, compatible_ranking, select
from .ports import IntroductionConflicts, IntroductionOwner, IntroductionStore, Receipt


def introduction_is_held(conflicts: IntroductionConflicts | None, *, repertoire_id: str,
                         presentation_id: str, presentation_revision: int) -> bool:
    """Delegate current suspension to its Study owner, without copying policy.

    No capability preserves the prior behavior. An injected capability must
    explicitly prove False to permit admission; unknown is never a release.
    """
    if conflicts is None:
        return False
    if type(presentation_revision) is not int or presentation_revision < 1:
        return True
    return conflicts.blocked(repertoire_id, presentation_id, presentation_revision) is not False


def repertoire_introduction_binding(snapshot: RepertoireSnapshot) -> str:
    """Bind actual repertoire content while excluding introduction chronology.

    #483's public complete snapshot is reread under the existing source fences.
    Scope/source revision, routes, boundaries, exact physical mappings and
    availability stay bound. Capture time, review/schedule facts and changing
    introduction evidence cannot replace content identity or create game proof.
    """
    from tempo_game_evidence_contracts.legacy import fingerprint
    from .ports import RepertoireIntroductionSource
    source = RepertoireIntroductionSource(snapshot)
    membership = source.snapshot.membership
    mappings = tuple({key:value for key,value in item.model_dump(mode='json').items()
                      if not key.startswith('introduc')}
                     for item in source.snapshot.decision_presentations)
    return fingerprint(dict(repertoire_id=source.repertoire_id,workspace_id=membership.workspace_id,
        source_revision=membership.revision,complete=membership.complete,available=membership.available,
        variant=source.snapshot.variant,learner_color=source.snapshot.learner_color,
        routes=tuple(route.model_dump(mode='json') for route in membership.routes),
        boundaries=tuple(item.model_dump(mode='json') for item in source.snapshot.boundaries),
        mappings=mappings))


@dataclass(frozen=True)
class TargetAlternatives:
    routes: tuple[ImmutableAuthoredRoute, ...]
    reason: str | None = None


def target_alternatives(routes, *, expected: tuple[tuple[str, str], ...], learner_color: str) -> TargetAlternatives:
    """Complete relevant prescriptions, retaining no unrelated positions/index.

    Reuse the current repertoire reader's finite 10k-route/100k-ply discovery
    limits. The existing 64-*relevant*-route guard is unchanged. No response
    filter is allowed before checking alternatives, including transpositions.
    """
    import chess
    from tempo_evidence_math import opening_position_key
    wanted = dict(expected)
    if not wanted or len(wanted) != len(expected):
        return TargetAlternatives((), 'target_context_unproved')
    responses = {position:set() for position in wanted}
    relevant, seen, plies = [], set(), 0
    try:
        for route in routes:
            if route.route_id in seen or len(seen) >= 10000:
                return TargetAlternatives((), 'target_discovery_bound')
            seen.add(route.route_id)
            plies += len(route.moves)
            if plies > 100000 or len(route.moves) > 1000:
                return TargetAlternatives((), 'target_discovery_bound')
            if route.learner_color != learner_color:
                return TargetAlternatives((), 'target_source_color_unproved')
            board = chess.Board(route.starting_fen)
            if not board.is_valid():
                return TargetAlternatives((), 'target_source_legality_unproved')
            touches = False
            for uci in route.moves:
                position = opening_position_key(board.fen())
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    return TargetAlternatives((), 'target_source_legality_unproved')
                if board.turn == (learner_color == 'white') and position in wanted:
                    touches = True
                    responses[position].add(uci)
                    if uci != wanted[position]:
                        return TargetAlternatives((), 'target_response_conflict')
                board.push(move)
            touches |= board.turn == (learner_color == 'white') and opening_position_key(board.fen()) in wanted
            if touches:
                relevant.append(route)
                if len(relevant) > 64:
                    return TargetAlternatives((), 'target_alternatives_bound')
    except (ValueError, TypeError, KeyError):
        return TargetAlternatives((), 'target_source_legality_unproved')
    if any(responses[position] != {move} for position,move in wanted.items()):
        return TargetAlternatives((), 'target_context_unproved')
    return TargetAlternatives(tuple(relevant))


def ordinary_encounter_allowance(*, study_only, global_limit, repertoire_limit,
                                introduction_count, repertoire_count):
    """Saved ordinary caps over canonical introductions, without D allocation.

    The queue owner counts each actual physical introduction today once.
    A shared presentation consumes one global slot and its admitting membership's
    ordinary allowance. No reserve, weight default or separate ledger is created.
    """
    if study_only:
        return 'study_only'
    if (type(global_limit) is not int or global_limit < 0
            or (repertoire_limit is not None and
                (type(repertoire_limit) is not int or repertoire_limit < 0))):
        return 'current_controls_unproved'
    if global_limit == 0 or repertoire_limit == 0:
        return 'zero_limit'
    limit = global_limit if repertoire_limit is None else repertoire_limit
    if (type(introduction_count) is not int or introduction_count < 0
            or type(repertoire_count) is not int or not 0 <= repertoire_count <= introduction_count):
        return 'current_controls_unproved'
    if introduction_count >= global_limit or repertoire_count >= limit:
        return 'introduction_allowance'
    return None


@dataclass(frozen=True)
class CommitResult:
    status: str
    receipt: Receipt | None = None
    reason: str | None = None


class Introductions:
    def __init__(self, store: IntroductionStore, owner: IntroductionOwner):
        self.store, self.owner = store, owner

    def accept_ranking(self, day: str, context: RankingContext, ranking: RankingOutput) -> str:
        """Receiving advisory data only saves facts, never touches active work."""
        self._day(day)
        self.store.lock_allocation()
        current = self.owner.facts(day).context
        if current != context or not compatible_ranking(ranking, context):
            return "rejected"
        self.store.save_ranking(context, ranking)
        return "accepted"

    @staticmethod
    def _day(day):
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("explicit local Study day required")

    def preview(self, day: str, *, priority: tuple[str, str] | None = None) -> Selection:
        self._day(day)
        facts = self.owner.facts(day)
        state = self.store.state(day)
        ranking = self.store.saved_ranking(facts.context)
        if ranking is not None and not compatible_ranking(ranking, facts.context):
            ranking = None
        spent = dict(facts.introduced_today)
        # Preserve each receipt's original credited membership. The ledger also
        # covers an introduction after queue rebuild or membership removal.
        for receipt in state.charges:
            spent[receipt.presentation_id] = receipt.repertoire_id
        return select(candidates=facts.candidates, ranking=ranking,
                      global_budget=facts.global_budget, weights=facts.weights,
                      spent=tuple(spent.items()), introduced=facts.introduced,
                      mandatory=facts.mandatory,
                      rotation_after=state.period_rotation_after if state.period_started else state.rotation_after,
                      enabled=facts.enabled, paused=facts.paused, study_only=facts.study_only,
                      priority=priority)

    def commit(self, day: str, candidate: Candidate, context: RankingContext, *,
               priority: tuple[str, str] | None = None) -> CommitResult:
        """Borrow one transaction: owner introduction + receipt + charge + rotation.

        Roll back the entire caller transaction on any exception. This method
        never opens/commits a transaction and does not manufacture a review.
        """
        self._day(day)
        self.store.lock_allocation()
        previous = self.store.receipt(candidate.presentation_id)
        if previous is not None:
            return CommitResult("replayed", previous)
        if self.owner.facts(day).context != context:
            return CommitResult("stale")
        plan = self.preview(day, priority=priority)
        # Enforce the published next allocation: a stale UI/rebuild cannot skip
        # ahead and consume another membership's fairness credit.
        if not plan.introductions or plan.introductions[0] != candidate:
            return CommitResult("stale")
        state = self.store.state(day)
        outcome = self.owner.introduce(day, candidate)
        if outcome != "introduced":
            return CommitResult(outcome)
        receipt = Receipt(candidate.presentation_id, day, candidate.repertoire_id)
        seed = state.period_rotation_after if state.period_started else state.rotation_after
        self.store.record_introduction(receipt, period_rotation_after=seed)
        return CommitResult("introduced", receipt)
