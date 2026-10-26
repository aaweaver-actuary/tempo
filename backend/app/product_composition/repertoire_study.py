"""C wires public current Repertoire facts into the sole Study conflict owner."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

from app.repertoire import build_index
from app.repertoire.changes.application import conflict_report, study_eligibility
from app.repertoire.changes.ports import ChangeError
from app.study.conflicts import (
    ConflictCommand, ConflictError, Occurrence, SnapshotConflictAuthority, SourceFence,
)
from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import current_snapshot as read_study_snapshot
from .repertoire_runtime import current_snapshot as approval_snapshot


def create_conflict_store(database):
    from tempo_legacy_database.adapters.study.conflicts import PostgresConflictStore
    return PostgresConflictStore(database)


def create_conflict_owner(database,*,authority,clock):
    from tempo_legacy_database.adapters.study.conflicts import current_study_conflicts
    return current_study_conflicts(database,authority=authority,clock=clock)


def _fence(snapshot):
    member=snapshot.membership
    return SourceFence(member.source_revision,member.revision,member.digest)


def study_snapshot(database,repertoire_id):
    try:
        return read_study_snapshot(database,repertoire_id)
    except ValueError as error:
        raise ChangeError(str(error)) from error


def current_study_block(database,repertoire_id,route_ids,**expected):
    """Read actual retained holds only while the displayed owner source is current."""
    snapshot=approval_snapshot(database,repertoire_id)
    if asdict(_fence(snapshot))!=expected:
        return None
    return create_conflict_store(database).block_state(repertoire_id,route_ids)


def synchronize_current_conflicts(database,repertoire_id):
    """One bounded command over exact current mappings, on the caller transaction.

    Unmapped source routes stay explicit pending facts. An old hold is offered
    for restoration only when the public current index still proves that exact
    route/ply/physical identity and revision. Repertoire decides eligibility;
    Study decides and writes all suspension/restoration effects.
    """
    snapshot=study_snapshot(database,repertoire_id)
    report=conflict_report(snapshot)
    if (not report.available or not report.source_complete
            or any(item.code!='own_response_conflict' for item in report.diagnostics)):
        raise ChangeError('current_conflict_source_unavailable')
    index=build_index(snapshot)
    store=create_conflict_store(database)
    current={}
    unmapped=[]
    for conflict in report.conflicts:
        for occurrence in conflict.occurrences:
            bindings=index.routes[occurrence.repertoire_id,occurrence.route_id][occurrence.ply].bindings
            if len(bindings)!=1:
                unmapped.append((occurrence.route_id,occurrence.ply))
                continue
            mapping=bindings[0]
            target=Occurrence(occurrence.repertoire_id,occurrence.route_id,occurrence.ply,
                              mapping.presentation_id,mapping.presentation_revision)
            current[target.identity()]=target
    retained=store.active_occurrences(repertoire_id,limit=16)
    if len(retained)==16:
        # The public page has no count/cursor result; a full page cannot prove
        # the operation covered every hold, so do not claim complete recovery.
        raise ChangeError('current_conflict_scope_exceeds_bound')
    for target in retained:
        route=index.routes.get((target.repertoire_id,target.route_id),())
        if (0<=target.ply<len(route) and any(
                item.presentation_id==target.presentation_id
                and item.presentation_revision==target.presentation_revision
                for item in route[target.ply].bindings)):
            current[target.identity()]=target
        else:
            unmapped.append((target.route_id,target.ply))
    if len(current)>16:
        raise ChangeError('current_conflict_scope_exceeds_bound')
    targets=tuple(sorted(current.values()))
    if not targets:
        return dict(status='pending' if unmapped else 'applied',receipts=[],
                    unmapped_occurrences=sorted(set(unmapped)))
    fence=_fence(snapshot)
    command_id='repertoire-audit:'+hashlib.sha256(json.dumps(
        [repertoire_id,asdict(fence),[asdict(item) for item in targets]],
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    authority=SnapshotConflictAuthority(
        current_snapshot=lambda identity:study_snapshot(database,identity),
        build_index=build_index,study_eligibility=study_eligibility)
    owner=create_conflict_owner(database,authority=authority,
                                  clock=lambda:datetime.now(timezone.utc))
    try:
        receipt=owner.synchronize(ConflictCommand(command_id,repertoire_id,fence,targets))
    except ConflictError as error:
        raise ChangeError(str(error)) from error
    return dict(status='pending' if unmapped else receipt.status,
                receipts=[asdict(receipt)],unmapped_occurrences=sorted(set(unmapped)))


def reconcile_after_publication(database,repertoire_id,_receipt):
    return synchronize_current_conflicts(database,repertoire_id)
