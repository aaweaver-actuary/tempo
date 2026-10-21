"""Fixed Analysis input operations and immutable/locked-scope storage guards."""
from functools import partial

from psycopg.types.json import Jsonb
from tempo_intelligence.errors import DomainError


class AnalysisInputs:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def manifest(self,value,payload,fingerprint):
        self._database.execute("INSERT INTO analysis_inputs(input_id,revision,payload,digest) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(value.input_id,value.revision,Jsonb(payload),fingerprint))
        row = self._database.execute("SELECT * FROM analysis_inputs WHERE input_id=%s AND revision=%s",(value.input_id,value.revision)).fetchone()
        if row["digest"] != fingerprint:
            raise DomainError("immutable_input_conflict")
        return row

    def page_input(self,value):
        row = self._database.execute("SELECT * FROM analysis_inputs WHERE input_id=%s AND revision=%s FOR UPDATE",(value.input_id,value.revision)).fetchone()
        if not row:
            raise DomainError("analysis_input_not_found",404)
        return row

    def page(self,value,payload,fingerprint):
        self._database.execute("INSERT INTO analysis_pages VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(value.input_id,value.revision,value.page_id,Jsonb(payload),fingerprint))
        saved = self._database.execute("SELECT digest FROM analysis_pages WHERE input_id=%s AND revision=%s AND page_id=%s",(value.input_id,value.revision,value.page_id)).fetchone()
        if saved["digest"] != fingerprint:
            raise DomainError("immutable_page_conflict")

    @property
    def receipt(self):
        return partial(self._owner._receipt,self._database)

    def finalization_scope(self,input_id,command):
        locked = self._database.execute("SELECT * FROM analysis_inputs WHERE input_id=%s AND revision=%s FOR UPDATE",(input_id,command.expected_revision)).fetchone()
        # Lock serializes page admission with finalization; pages already accepted are immutable.
        pages = self._database.execute("SELECT page_id FROM analysis_pages WHERE input_id=%s AND revision=%s",(input_id,command.expected_revision)).fetchall()
        if {p["page_id"] for p in pages} != {p["page_id"] for p in locked["payload"]["pages"]}:
            raise DomainError("analysis_input_incomplete")

    def accept(self,input_id,command):
        self._database.execute("UPDATE analysis_inputs SET state='accepted' WHERE input_id=%s AND revision=%s",(input_id,command.expected_revision))

    @property
    def save_receipt(self):
        return partial(self._owner._save_receipt,self._database)
