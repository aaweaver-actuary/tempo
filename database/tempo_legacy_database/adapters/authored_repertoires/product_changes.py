"""Repertoire approval storage. Borrowed transaction, no connection or DDL startup.

Outer composition supplies the owning current-source projection and publication
command. No queue, review, scheduling or physical-card table is written here.
SCHEMA_SQL is a disposable-fixture / integration-migration handoff, never auto-run.
"""
from contextlib import contextmanager
from typing import Callable

from app.contracts.product_v02.analysis import Proposal, RepertoireChangeReceipt
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire.changes.ports import ChangeError, ValidatedChange

SCHEMA_SQL = """
CREATE TABLE product_repertoire_previews (
    repertoire_id text NOT NULL REFERENCES repertoires(id),
    proposal_id text NOT NULL,
    proposal_revision bigint NOT NULL CHECK (proposal_revision > 0),
    display_digest text NOT NULL,
    proposal_json text NOT NULL,
    PRIMARY KEY (repertoire_id, proposal_id)
);
CREATE TABLE product_repertoire_change_receipts (
    repertoire_id text NOT NULL,
    proposal_id text NOT NULL,
    receipt_json text NOT NULL,
    before_json text,
    after_json text,
    PRIMARY KEY (repertoire_id, proposal_id),
    FOREIGN KEY (repertoire_id, proposal_id)
        REFERENCES product_repertoire_previews(repertoire_id, proposal_id),
    CHECK ((before_json IS NULL) = (after_json IS NULL))
);
"""


def lock_sources(database, repertoire_id: str) -> tuple[dict, ...]:
    """Current authored sources, bounded and serialized with repertoire writers."""
    if database.execute_native("SELECT id FROM repertoires WHERE id=%s FOR UPDATE",
                               (repertoire_id,)).fetchone() is None:
        return ()
    rows = database.execute_native("""
        SELECT 'line:' || id source_id, start_fen, moves_json, trained_color
        FROM repertoire_lines WHERE repertoire_id=%s
        UNION ALL
        SELECT 'card:' || card.id, card.start_fen, card.moves_json,
            COALESCE(card.trained_color, (SELECT line.trained_color FROM repertoire_lines line
                WHERE line.repertoire_id=%s ORDER BY line.created_at,line.id LIMIT 1))
        FROM cards card WHERE card.archived=0 AND card.content_type='opening'
            AND card.superseded_by IS NULL AND (card.repertoire_id=%s OR EXISTS(
                SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=%s AND link.card_id=card.id))
        ORDER BY source_id LIMIT 10001
        """, (repertoire_id, repertoire_id, repertoire_id, repertoire_id)).fetchall()
    if len(rows) > 10000:
        raise ChangeError("source_exceeds_bound")
    return tuple(dict(row) for row in rows)


class PostgresChangeStore:
    """One scoped owner store; caller keeps connection and outer transaction.

    reader/publisher borrow this same connection and existing repertoire row lock.
    Publisher must use the current content/Study publication owner for additions,
    preserve review history, and return only after all effects are enlisted here.
    """
    def __init__(self, connection, repertoire_id: str,
                 reader: Callable[[object, str], RepertoireSnapshot],
                 publisher: Callable[[object, ValidatedChange], None]):
        self.connection = connection
        self.repertoire_id = repertoire_id
        self.reader = reader
        self.publisher = publisher

    @contextmanager
    def transaction(self, repertoire_id: str):
        if repertoire_id and repertoire_id != self.repertoire_id:
            raise ChangeError("repertoire_scope_mismatch")
        # psycopg transaction is a savepoint inside an existing transaction; no
        # independently committing repository can split acceptance and receipt.
        with self.connection.transaction():
            self.connection.execute("SET LOCAL lock_timeout = '2s'")
            self.connection.execute("SET LOCAL statement_timeout = '5s'")
            row = self.connection.execute("SELECT id FROM repertoires WHERE id=%s FOR UPDATE",
                                           (self.repertoire_id,)).fetchone()
            if row is None:
                raise ChangeError("repertoire_missing")
            yield _Transaction(self)


class _Transaction:
    def __init__(self, store):
        self.store = store
        self.connection = store.connection
        self.change = None

    def snapshot(self):
        return self.store.reader(self.connection, self.store.repertoire_id)

    def preview(self, proposal_id):
        row = self.connection.execute("SELECT proposal_json FROM product_repertoire_previews "
            "WHERE repertoire_id=%s AND proposal_id=%s FOR UPDATE",
            (self.store.repertoire_id, proposal_id)).fetchone()
        return Proposal.model_validate_json(row[0]) if row else None

    def save_preview(self, proposal):
        if proposal.repertoire_id != self.store.repertoire_id:
            raise ChangeError("repertoire_scope_mismatch")
        self.connection.execute("INSERT INTO product_repertoire_previews "
            "(repertoire_id,proposal_id,proposal_revision,display_digest,proposal_json) "
            "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(repertoire_id,proposal_id) DO UPDATE SET "
            "proposal_revision=excluded.proposal_revision,display_digest=excluded.display_digest,"
            "proposal_json=excluded.proposal_json",
            (proposal.repertoire_id, proposal.proposal_id, proposal.proposal_revision,
             proposal.display_digest, proposal.model_dump_json()))

    def receipt(self, proposal_id):
        row = self.connection.execute("SELECT receipt_json FROM product_repertoire_change_receipts "
            "WHERE repertoire_id=%s AND proposal_id=%s", (self.store.repertoire_id, proposal_id)).fetchone()
        return RepertoireChangeReceipt.model_validate_json(row[0]) if row else None

    def publish(self, change):
        self.store.publisher(self.connection, change)
        published = self.snapshot()
        if (published.membership.revision, published.membership.digest) != (
                change.after.membership.revision, change.after.membership.digest):
            raise ChangeError("publication_not_enlisted")
        self.change = change

    def save_receipt(self, receipt):
        self.connection.execute("INSERT INTO product_repertoire_change_receipts "
            "(repertoire_id,proposal_id,receipt_json,before_json,after_json) VALUES(%s,%s,%s,%s,%s)",
            (self.store.repertoire_id, receipt.proposal_id, receipt.model_dump_json(),
             self.change.before.model_dump_json() if self.change else None,
             self.change.after.model_dump_json() if self.change else None))
