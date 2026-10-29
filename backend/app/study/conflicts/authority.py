"""Mechanical adapter to injected public Repertoire read/index/eligibility APIs.

Repertoire still decides one-response conflicts. No Repertoire implementation,
database, provider or composition import belongs in this Study module.
"""
from .application import Assessment, ConflictError, Eligibility, SourceFence


class SnapshotConflictAuthority:
    def __init__(self, *, current_snapshot, build_index, study_eligibility):
        self.current_snapshot=current_snapshot
        self.build_index=build_index
        self.study_eligibility=study_eligibility

    def assess(self, command):
        # Store source reservations precede this fresh read. Composition binds
        # current_snapshot to the SAME borrowed writer, never a producer value.
        snapshot=self.current_snapshot(command.repertoire_id)
        member=snapshot.membership
        fence=SourceFence(member.source_revision,member.revision,member.digest)
        if fence!=command.expected:
            return Assessment(fence,())
        index=self.build_index(snapshot)
        decisions=[]
        for target in command.occurrences:
            route=index.routes.get((target.repertoire_id,target.route_id),())
            if not 0<=target.ply<len(route):
                raise ConflictError('current_occurrence_unproved')
            bindings=route[target.ply].bindings
            if not any(item.presentation_id==target.presentation_id
                       and item.presentation_revision==target.presentation_revision for item in bindings):
                raise ConflictError('current_physical_occurrence_unproved')
            decision=self.study_eligibility(snapshot,repertoire_id=target.repertoire_id,
                route_id=target.route_id,ply=target.ply,source_revision=fence.source_revision,
                revision=fence.revision,digest=fence.digest)
            decisions.append(Eligibility(target,decision.eligible,decision.reason))
        return Assessment(fence,tuple(decisions))
