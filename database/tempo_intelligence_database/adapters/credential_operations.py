"""Fixed registration/head operations and atomic guards on the owner's context."""
from tempo_intelligence.errors import DomainError


class CredentialOperations:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def row(self):
        return self._database.execute("SELECT h.generation,h.registration_id,r.status,r.expires_at FROM credential_head h LEFT JOIN credential_registrations r USING(registration_id) WHERE h.id=1").fetchone()

    def status(self):
        return self._owner._credential_status(self._database)

    def head(self,expected_generation):
        head=self._database.execute("SELECT * FROM credential_head WHERE id=1 FOR UPDATE").fetchone()
        if head["generation"] != expected_generation:
            raise DomainError("credential_generation_conflict")
        return head

    def registration(self,material):
        return self._database.execute("SELECT * FROM credential_registrations WHERE registration_id=%s",(material.registration_id,)).fetchone()

    def registration_replay(self,head,material,old):
        if head["registration_id"] == material.registration_id and old["status"] in ("registered","accepted"):
            return self._owner._credential_status(self._database)
        raise DomainError("registration_id_retired")

    def register(self,material,generation):
        self._database.execute("INSERT INTO credential_registrations(registration_id,generation,status,expires_at) VALUES(%s,%s,'registered',%s)",(material.registration_id,generation,material.expires_at))
        self._database.execute("UPDATE credential_head SET generation=%s,registration_id=%s WHERE id=1",(generation,material.registration_id))
        self._database.execute("UPDATE caches SET state='queued',error_code=NULL,next_attempt_at=NULL WHERE state='waiting_credentials'")

    def revoke(self,head):
        self._database.execute("UPDATE credential_registrations SET status='revoked' WHERE registration_id=%s",(head["registration_id"],))
