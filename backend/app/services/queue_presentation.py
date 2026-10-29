"""Current Study presentation facts without loading seeding or task orchestration.

Reads use the caller's borrowed transaction or bounded reader. This module does
not select or admit work.
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
    from tempo_legacy_database.adapters.study.recombination import regrouped_presentation_ids
    regrouped = regrouped_presentation_ids(database, (row['card_id'] for row in rows), read=read)
    game_priority_reasons = {'product_game_lapse', 'product_game_encounter'}
    prioritized_card_ids = tuple(row['card_id'] for row in rows
        if row['gameplay_priority_reason'] in game_priority_reasons)
    owed_card_ids = set()
    if prioritized_card_ids:
        from tempo_legacy_database.adapters.study.game_feedback import owed_feedback_card_ids
        owed_card_ids = owed_feedback_card_ids(database, prioritized_card_ids, read=read, include_regrouped=bool(regrouped))
    for row in rows:
        if row['gameplay_priority_reason'] in game_priority_reasons and row['card_id'] not in owed_card_ids:
            row['gameplay_priority_reason'] = None
        from .legacy_game_evidence_scope import BRIDGE_MISS_REASON
        if presentation_storage.is_postgres_connection(database) and row['gameplay_priority_reason'] == BRIDGE_MISS_REASON:
            from .legacy_study_remediation import has_current_miss
            if not has_current_miss(database, row['card_id']): row['gameplay_priority_reason'] = None
        row['admission_kind'] = ('explicit' if row['admission_kind']=='explicit'
                                 else 'review' if row.get('cycle',0)>0 or row['card_id'] in reviewed or row['card_id'] in regrouped else 'new')
        row['eligible'] = row['id'] in eligible
    return rows
