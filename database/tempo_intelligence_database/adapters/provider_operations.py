"""Fixed provider-source leases and evidence writes on the owner's transaction."""
from functools import partial


class ProviderOperations:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def control(self):
        return self._database.execute("SELECT * FROM provider_control WHERE id=1 FOR UPDATE SKIP LOCKED").fetchone()

    def cache(self):
        return self._database.execute("SELECT * FROM caches WHERE kind<>'maia' AND descriptor->>'provider_origin'=%s AND ((state IN ('queued','retry_wait','waiting_credentials') AND (next_attempt_at IS NULL OR next_attempt_at<=now())) OR (state='running' AND leased_until<=now())) ORDER BY updated_at,cache_id FOR UPDATE SKIP LOCKED LIMIT 1",(self._owner.configuration.provider_base_url,)).fetchone()

    def credential_status(self):
        return self._owner._credential_status(self._database)

    def wait_for_credentials(self,credential,cache):
        self._database.execute("UPDATE caches SET state='waiting_credentials',error_code=%s,next_attempt_at=now()+interval '60 seconds',lease_token=NULL,leased_until=NULL WHERE cache_id=%s",(credential["status"],cache["cache_id"]))

    def lease_cache(self,lease,cache):
        return self._database.execute("UPDATE caches SET state='running',execution_generation=execution_generation+1,lease_token=%s,leased_until=now()+interval '60 seconds',updated_at=now() WHERE cache_id=%s RETURNING *",(lease,cache["cache_id"])).fetchone()

    def lease_provider(self,lease):
        self._database.execute("UPDATE provider_control SET lease_token=%s,leased_until=now()+interval '60 seconds' WHERE id=1",(lease,))

    def lock(self):
        self._database.execute("SELECT id FROM provider_control WHERE id=1 FOR UPDATE")

    def fenced(self,claim):
        return self._owner._fenced(self._database,claim)

    @property
    def append(self):
        return partial(self._owner._append,self._database)

    def accept_credential(self,credential):
        self._database.execute("UPDATE credential_registrations SET status='accepted' WHERE registration_id=%s AND generation=%s AND status='registered'",(credential["registration_id"],credential["generation"]))

    def release(self,claim):
        self._owner._release_provider(self._database,claim)
