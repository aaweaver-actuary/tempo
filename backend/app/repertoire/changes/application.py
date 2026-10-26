"""Pure conflict audit/eligibility; optional producers and infrastructure absent."""
from __future__ import annotations

from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire import build_index

from .ports import (AffectedOccurrence, ChangeError, ConflictReport,
                    EligibilityDecision, ResponseConflict)


def conflict_report(snapshot: RepertoireSnapshot) -> ConflictReport:
    """Use the public exact-position index, retaining invalid-source diagnostics."""
    index = build_index(snapshot)
    conflicts = tuple(ResponseConflict(node.key, node.own_responses, tuple(
        AffectedOccurrence(item.key.repertoire_id, item.route_id, item.ply,
            item.next_move, tuple(sorted({binding.presentation_id for binding in item.bindings})))
        for item in node.occurrences if item.learner_turn and item.next_move
    )) for node in index.nodes.values() if node.conflict)
    return ConflictReport(index.snapshot_id, index.source_revision, index.revision,
        index.digest, conflicts, index.diagnostics, index.available, snapshot.membership.complete)


def study_eligibility(snapshot: RepertoireSnapshot, *, repertoire_id: str,
                      route_id: str, ply: int, source_revision: str,
                      revision: int, digest: str) -> EligibilityDecision:
    """Read-only owner decision. Study alone suspends attempts, without Again.

    Every use supplies the current source fence; an unpublished/old result cannot
    clear a block. Occurrence scope never globally blocks a shared physical card.
    """
    membership = snapshot.membership
    if (source_revision, revision, digest) != (membership.source_revision, membership.revision, membership.digest):
        return EligibilityDecision(False, "stale_integrity", True)
    if not membership.available or not membership.complete:
        return EligibilityDecision(False, "unpublished_integrity", True)
    index = build_index(snapshot)
    occurrences = index.routes.get((repertoire_id, route_id), ())
    if not 0 <= ply < len(occurrences):
        return EligibilityDecision(False, "unknown_occurrence", True)
    occurrence = occurrences[ply]
    if not occurrence.learner_turn or not occurrence.next_move:
        return EligibilityDecision(False, "not_own_decision", True)
    # A malformed source route cannot gain eligibility from its retained prefix.
    errors = [item for item in index.diagnostics
              if item.repertoire_id == repertoire_id and item.route_id == route_id
              and item.code != "own_response_conflict"]
    if errors:
        return EligibilityDecision(False, "invalid_source", True)
    if index.nodes[occurrence.key].conflict:
        return EligibilityDecision(False, "own_response_conflict", True)
    return EligibilityDecision(True, None, False)


def require_valid_change(snapshot: RepertoireSnapshot) -> None:
    report = conflict_report(snapshot)
    if not report.clean:
        raise ChangeError("conflict_pending_resolution" if report.conflicts else "invalid_source")
