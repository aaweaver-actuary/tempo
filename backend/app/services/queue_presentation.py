"""Current Study presentation facts without loading seeding or task orchestration.

Reads use the caller's borrowed transaction or bounded reader. This module does
not select or admit work.
"""

from tempo_storage.queue_refresh import (
    presentation as presentation_storage,
)

MISS_REASON = "Priority review · missed in a recent game"


def presentation_rows(database, day, *, read=None, after_entry_id=None, limit=None):
    """Capture eligibility and attempt protection without changing membership."""
    rows, reviewed, eligible = presentation_storage.presentation_facts(
        database,
        day,
        MISS_REASON,
        read=read,
        after_entry_id=after_entry_id,
        limit=limit,
    )
    if not rows:
        return rows
    for row in rows:
        # Automatic game feedback is retired; historical priority is inert.
        row["gameplay_priority_reason"] = None
        row["admission_kind"] = (
            "explicit"
            if row["admission_kind"] == "explicit"
            else "review"
            if row.get("cycle", 0) > 0
            or row["card_id"] in reviewed
            or row.get("retained_generated", False)
            else "new"
        )
        row["eligible"] = row["id"] in eligible
    return rows
