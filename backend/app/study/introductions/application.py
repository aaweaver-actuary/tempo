"""Offline-safe ranking consumption and atomic Study-owned introduction effects."""
from dataclasses import dataclass
from datetime import date

from app.contracts.product_v02.analysis import RankingOutput
from app.contracts.product_v02.repertoire import RepertoireSnapshot
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
class CommitResult:
    status: str
    receipt: Receipt | None = None


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
                      enabled=facts.enabled, paused=facts.paused, study_only=facts.study_only, priority=priority)

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
