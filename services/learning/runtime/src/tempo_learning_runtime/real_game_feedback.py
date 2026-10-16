"""Learning's retained queue readers use remediation's authoritative predicate."""

from tempo_learning_remediation import current_miss_sql

MISS_REASON = "Priority review · missed in a recent game"


def outstanding_miss_sql(*, postgres=False, columns="DISTINCT event.card_id", additional_where=""):
    # This compatibility query shape is consumed by the existing frozen reader.
    # Only the workspace expression is substituted; card/request values stay bound.
    predicate = current_miss_sql("card").replace("%s", "(SELECT workspace_id FROM instance WHERE id=1)")
    return f"""SELECT {columns} FROM remediation_cards event JOIN cards card ON card.id=event.card_id
      WHERE event.workspace_id=(SELECT workspace_id FROM instance WHERE id=1) AND {predicate}
        AND card.serving_active=1 AND card.archived=0 AND card.pending_validation=0
        AND card.superseded_by IS NULL
        AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)
        {additional_where}"""


def has_outstanding_real_game_miss(database, card_id):
    return database.execute(outstanding_miss_sql(columns="1", additional_where="AND card.id=? LIMIT 1"), (card_id,)).fetchone() is not None
