"""Current Study presentation facts without loading seeding or task orchestration.

All reads use the borrowed transaction. This module does not select or admit work.
"""
from tempo_legacy_database.adapters.queue_refresh import presentation as presentation_storage
from .real_game_feedback import MISS_REASON


def presentation_rows(database, day, *, read=None, after_entry_id=None, limit=None):
    """Capture eligibility and attempt protection without changing membership."""
    rows, reviewed, eligible = presentation_storage.presentation_facts(
        database, day, MISS_REASON, read=read, after_entry_id=after_entry_id, limit=limit,
    )
    if not rows:
        return rows
    for row in rows:
        if row['gameplay_priority_reason'] in {'product_game_lapse','product_game_encounter'}:
            from tempo_legacy_database.adapters.study.game_feedback import has_owed_feedback
            if not has_owed_feedback(database, row['card_id']):
                row['gameplay_priority_reason'] = None
        from .legacy_game_evidence_scope import BRIDGE_MISS_REASON
        if presentation_storage.is_postgres_connection(database) and row['gameplay_priority_reason'] == BRIDGE_MISS_REASON:
            from .legacy_study_remediation import has_current_miss
            if not has_current_miss(database, row['card_id']): row['gameplay_priority_reason'] = None
        row['admission_kind'] = ('explicit' if row['admission_kind']=='explicit'
                                 else 'review' if row.get('cycle',0)>0 or row['card_id'] in reviewed else 'new')
        row['eligible'] = row['id'] in eligible
    return rows
