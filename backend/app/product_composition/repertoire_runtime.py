"""Actual C owner bindings; no state query/mutation or optional worker startup."""
from __future__ import annotations

from fastapi import HTTPException

from app.repertoire.changes.retained import current_snapshot as read_current_snapshot, current_card_definitions, retained_changes
from app.repertoire.changes.ports import ChangeError
from .repertoire_commands import create_current_repertoire_http, register_repertoire_commands


def current_snapshot(database,repertoire_id):
    try:
        return read_current_snapshot(database,repertoire_id)
    except ValueError as error:
        raise ChangeError(str(error)) from error


def retained_changes_factory(*, after_publication):
    """Keep source approval and Study reconciliation on the gateway transaction."""
    def factory(database,repertoire_id):
        owner=retained_changes(database,repertoire_id,current_snapshot=current_snapshot)

        class Changes:
            def preview(self,proposal):
                return owner.preview(proposal)

            def decide(self,decision):
                if decision.action=="accept" and after_publication is None:
                    raise HTTPException(503,"Current Study conflict reconciliation is unavailable")
                receipt=owner.decide(decision)
                if receipt.status=="accepted":
                    # Owner failure propagates: the existing gateway rolls the
                    # borrowed source/receipt/Study transaction back together.
                    after_publication(database,repertoire_id,receipt)
                return receipt

        return Changes()
    return factory


def create_retained_repertoire_router(*, current_study_block):
    """Current Repertoire reads, existing foreground command dispatch, real status."""
    from tempo_legacy_database.connections import read_connection
    from app.command_dispatch import dispatch_command

    return create_current_repertoire_http(reader=read_connection,
        current_snapshot=current_snapshot, dispatch=dispatch_command,
        current_study_block=current_study_block,current_card_definitions=current_card_definitions)


def register_retained_repertoire_commands(*, audit_for_transaction, after_publication):
    """Explicit worker composition only; both effects belong to actual owners."""
    register_repertoire_commands(retained_changes_factory(after_publication=after_publication),
                                 audit_for_transaction=audit_for_transaction)


def current_repertoire_router():
    from .repertoire_study import current_study_block
    return create_retained_repertoire_router(current_study_block=current_study_block)


def register_current_repertoire_commands():
    from .repertoire_study import reconcile_after_publication, synchronize_current_conflicts
    register_retained_repertoire_commands(audit_for_transaction=synchronize_current_conflicts,
                                         after_publication=reconcile_after_publication)


def is_repertoire_command_path(method,path_parts):
    return (method=='POST' and path_parts[:3]==['api','product','repertoires']
        and ((len(path_parts)==5 and path_parts[4]=='audit')
             or (len(path_parts)==6 and path_parts[4]=='changes'
                 and path_parts[5] in {'preview','decide'})))
