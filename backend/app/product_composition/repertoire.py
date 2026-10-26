"""Proposal commands go unchanged to the single approved-change owner."""
from app.contracts.product_v02.analysis import (
    Proposal, ProposalDecision, RepertoireChangePort, RepertoireChangeReceipt,
)
from app.contracts.product_v02.repertoire import RepertoireSnapshotPort
from app.repertoire.changes.http import routes


def create_repertoire_router(changes, snapshots: RepertoireSnapshotPort):
    """Root owner mounts this router during its exact-file reservation."""
    return routes(changes, snapshots)


class RepertoireCommands:
    """Shared C/E approval entry; no content, Study or engine policy lives here."""
    def __init__(self, changes: RepertoireChangePort):
        self.changes = changes

    def preview(self, proposal: Proposal) -> RepertoireChangeReceipt:
        return self.changes.preview(proposal)

    def decide(self, decision: ProposalDecision) -> RepertoireChangeReceipt:
        # The owner binds source/display revisions, reevaluates edits, validates
        # one response, and atomically applies displayed content/card additions.
        return self.changes.decide(decision)
