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
        """UPDATE cards SET state='new',due_date=?
           WHERE unlock_after_card_id=? AND state='locked' AND (
               (content_type='opening' AND EXISTS(
                   SELECT 1 FROM reviews WHERE card_id=? AND source_kind='study'
                       AND invalidated_at IS NULL
               ) AND NOT EXISTS(
                   SELECT 1 FROM opening_graph_steps step
                   JOIN opening_graph_publications publication
                     ON publication.repertoire_id=step.repertoire_id
                    AND publication.generation=step.generation
                   WHERE step.card_id=cards.id
               )) OR (content_type!='opening' AND ?='mature')
           )""",
        (review_day, card_id, card_id, state),
    )
