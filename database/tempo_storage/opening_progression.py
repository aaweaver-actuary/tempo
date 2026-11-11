"""Completed study exposure is separate from FSRS maturity."""

# Shared by the sparse selectors and their publication-time recheck. Keep the
# parent alias stable so SQLite and native PostgreSQL observe identical evidence.
PRACTICED_OPENING_PARENT_SQL = """EXISTS(
    SELECT 1 FROM reviews parent_review
    WHERE parent_review.card_id=parent.id AND parent_review.source_kind='study'
      AND parent_review.invalidated_at IS NULL
)"""


def unlock_legacy_children_after_review(database, card_id: str, review_day: str, state: str) -> None:
    """Preserve legacy links without overriding a published graph's route order."""
    database.execute(
        "UPDATE cards SET state='new',due_date=%s\n           WHERE unlock_after_card_id=%s AND state='locked' AND (\n               (content_type='opening' AND EXISTS(\n                   SELECT 1 FROM reviews WHERE card_id=%s AND source_kind='study'\n                       AND invalidated_at IS NULL\n               ) AND NOT EXISTS(\n                   SELECT 1 FROM opening_graph_steps step\n                   JOIN opening_graph_publications publication\n                     ON publication.repertoire_id=step.repertoire_id\n                    AND publication.generation=step.generation\n                   WHERE step.card_id=cards.id\n               )) OR (content_type!='opening' AND %s='mature')\n           )",
        (review_day, card_id, card_id, state),
    )
