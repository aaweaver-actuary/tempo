"""Existing Learning/remediation integration reads and borrowed driver facade."""
from psycopg.rows import tuple_row


class BorrowedConnection:
    """Per-cursor tuple rows without changing the runtime's shared row factory."""

    def __init__(self, database):
        self.database = database
        self.info = database.raw.info

    def execute(self, statement, parameters=()):
        return self.database.raw.cursor(row_factory=tuple_row).execute(
            statement, parameters
        )


def card_publication_identity(database, card_id):
    return database.execute(
        "SELECT publication_id,publication_generation FROM cards WHERE id=?",
        (card_id,),
    ).fetchone()


def content_alias(database, card_id, publication_id):
    return database.execute("SELECT content_card_id FROM content_card_aliases WHERE learning_card_id=? AND publication_id=?", (card_id,publication_id)).fetchone()


def accepted_publication_facts(database, card_id, publication_id, generation):
    return database.execute(
        """SELECT publication.payload_json,card.revision,card.serving_active,card.archived,
                      card.superseded_by FROM cards card
               JOIN accepted_publications publication ON publication.publication_id=card.publication_id
                 AND publication.generation=card.publication_generation
               JOIN serving_publications serving ON serving.publication_id=publication.publication_id
                 AND serving.generation=publication.generation
               WHERE card.id=? AND card.publication_id=? AND card.publication_generation=?
                 AND publication.state='active'""",
        (card_id, publication_id, generation),
    ).fetchone()


def clear_priority(database, card_id, miss_reason):
    database.execute(
        "UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE card_id=? AND gameplay_priority_reason=?",
        (card_id, miss_reason),
    )


def withdrawal_card(database, workspace_id, obligation_id):
    return database.execute_native(
        "SELECT card_id FROM remediation_obligation_heads WHERE workspace_id=%s AND obligation_id=%s",
        (workspace_id, obligation_id),
    ).fetchone()


def workspace_revision(database, workspace_id):
    return database.execute_native(
        "SELECT revision FROM remediation_workspaces WHERE workspace_id=%s",
        (workspace_id,),
    ).fetchone()


def attempt_issued_at(database, workspace_id, identity):
    return database.execute_native(
        "SELECT issued_at FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s",
        (workspace_id, identity),
    ).fetchone()


def bound_attempt(database, workspace_id, identity):
    return database.execute_native(
        """SELECT attempt_id,card_id,card_revision,publication_id,publication_generation,issued_at
           FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s""",
        (workspace_id, identity),
    ).fetchone()


def settlement_review(database, review_id, card_id):
    return database.execute(
        "SELECT rating,guided,reviewed_at,source_kind,invalidated_at FROM reviews WHERE id=? AND card_id=?",
        (review_id, card_id),
    ).fetchone()


def issued_origin(database, queue_entry_id, card_id, revision):
    return database.execute(
        "SELECT attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=? AND revision=? AND issued_as_head=1",
        (queue_entry_id, card_id, revision),
    ).fetchone()


def card(database, card_id):
    return database.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()


def opening_route(database, card_id, generation):
    return database.execute(
        """SELECT step.repertoire_id FROM opening_graph_steps step
           JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
             AND publication.generation=step.generation
           JOIN repertoires repertoire ON repertoire.id=step.repertoire_id
           JOIN repertoire_lines line ON line.id=step.line_id
           WHERE step.card_id=? AND step.generation=? AND publication.state='ready'
             AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                 WHERE block.card_id=step.card_id AND block.repertoire_id=step.repertoire_id)
           ORDER BY repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index LIMIT 1""",
        (card_id, generation),
    ).fetchone()


def queue_entries(database, card_id, queue_day):
    return database.execute(
        "SELECT id,status FROM daily_queue WHERE card_id=? AND queue_date=?",
        (card_id, queue_day),
    ).fetchall()


def deleted_card(database, card_id):
    return database.execute(
        "SELECT 1 FROM deleted_cards WHERE card_id=?", (card_id,)
    ).fetchone()


def admitted_entry(database, card_id, day):
    return database.execute(
        "SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued' ORDER BY position,id LIMIT 1",
        (card_id, day),
    ).fetchone()
