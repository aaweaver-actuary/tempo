"""Content bridge SQL and locked row projections on the original owner transactions.

Application approval/export/proposal decisions remain in tempo_content.bridge.application.
Mutations borrow the supplied transaction; only existing service read helpers open reads.
"""
from psycopg.types.json import Jsonb
from tempo_content.domain import ContentError


def lock_scope(database, source_id, operation_id=None):
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("content:scope:" + source_id,))
    reservation = database.execute("SELECT id FROM content.bridge_operations WHERE source_id=%s AND state IN ('approved','applying')", (source_id,)).fetchone()
    if reservation and reservation["id"] != operation_id:
        raise ContentError("source_reserved", "An approved operation owns this source; inspect/reconcile it first")


def source_scope_lock(database, source_id):
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("content:scope:" + source_id,))


class BridgeStore:
    def __init__(self, service):
        self.service = service

    lock_scope = staticmethod(lock_scope)
    source_scope_lock = staticmethod(source_scope_lock)

    def insert_draft(self, database, identifier, source_id, source_key, expected_revision, fingerprint, document):
        database.execute("INSERT INTO content.bridge_drafts VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (identifier, source_id, source_key, expected_revision, fingerprint, Jsonb(document)))

    def draft_for_proposal(self, proposal_id, workspace_id, source_key):
        return self.service._one("SELECT * FROM content.bridge_drafts WHERE document->'proposal'->>'proposal_id'=%s AND document->'proposal'->>'workspace_id'=%s AND source_key=%s",
                (proposal_id, workspace_id, source_key))

    def draft(self, identifier):
        return self.service._one("SELECT * FROM content.bridge_drafts WHERE id=%s", (identifier,))

    def operation_for_draft(self, database, identifier):
        return database.execute("SELECT id FROM content.bridge_operations WHERE draft_id=%s", (identifier,)).fetchone()

    def source_revision(self, database, source_id):
        return database.execute("SELECT requested_revision FROM content.sources WHERE id=%s", (source_id,)).fetchone()

    def insert_operation(self, database, operation_id, identifier, source_id):
        database.execute("INSERT INTO content.bridge_operations(id,draft_id,source_id,state) VALUES(%s,%s,%s,'approved')",
                    (operation_id, identifier, source_id))

    def operation(self, identifier):
        return self.service._one("SELECT o.*,d.digest,d.source_key,d.expected_revision,d.document AS draft FROM content.bridge_operations o JOIN content.bridge_drafts d ON d.id=o.draft_id WHERE o.id=%s", (identifier,))

    def operation_document_for_update(self, database, identifier):
        return database.execute("SELECT state,document FROM content.bridge_operations WHERE id=%s FOR UPDATE", (identifier,)).fetchone()

    def operation_state_for_update(self, database, identifier):
        return database.execute("SELECT state FROM content.bridge_operations WHERE id=%s FOR UPDATE", (identifier,)).fetchone()

    def mark_failed(self, database, identifier, failure):
        database.execute("UPDATE content.bridge_operations SET state='failed',document=document || %s,updated_at=now() WHERE id=%s", (Jsonb({'failure': failure}), identifier))

    def mark_cancelled(self, database, identifier):
        database.execute("UPDATE content.bridge_operations SET state='cancelled',updated_at=now() WHERE id=%s", (identifier,))

    def mark_applying(self, database, identifier):
        database.execute("UPDATE content.bridge_operations SET state='applying',updated_at=now() WHERE id=%s", (identifier,))

    def merge_prepared_document(self, database, identifier, prepared):
        database.execute("UPDATE content.bridge_operations SET document=document || %s,updated_at=now() WHERE id=%s AND state='applying'", (Jsonb(prepared), identifier))

    def mark_completed(self, database, identifier, receipt):
        database.execute("UPDATE content.bridge_operations SET state='completed',document=document || %s,updated_at=now() WHERE id=%s", (Jsonb({"receipt": receipt}), identifier))

    def merge_checkpoint(self, database, identifier, values):
        database.execute("UPDATE content.bridge_operations SET document=document || %s,updated_at=now() WHERE id=%s", (Jsonb(values),identifier))

    def legacy_export(self, publication_id, generation):
        return self.service._one("SELECT document->'export' AS export FROM content.bridge_operations WHERE document->'export'->'publication'->>'publication_id'=%s AND document->'export'->'publication'->>'publication_generation'=%s", (publication_id,str(generation)))

    def packed_export(self, publication_id, generation):
        return self.service._one("SELECT document->'export_packed' AS packed FROM content.bridge_operations WHERE document->'export_identity'->>'publication_id'=%s AND document->'export_identity'->>'publication_generation'=%s",(publication_id,str(generation)))

    def publication(self, publication_id, generation):
        return self.service._one("SELECT * FROM content.publications WHERE publication_id=%s AND generation=%s", (publication_id, generation))

    def proposal_draft_identity(self, proposal_id, workspace_id, source_key):
        return self.service._one("SELECT id,document FROM content.bridge_drafts WHERE document->'proposal'->>'proposal_id'=%s AND document->'proposal'->>'workspace_id'=%s AND source_key=%s", (proposal_id,workspace_id,source_key))

    def latest_completed_provenance(self, source_id):
        return self.service._one("SELECT d.document FROM content.bridge_operations o JOIN content.bridge_drafts d ON d.id=o.draft_id WHERE o.source_id=%s AND o.state='completed' ORDER BY o.updated_at DESC LIMIT 1", (source_id,))
