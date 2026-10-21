"""Fixed guarded request-command operations on the caller's write transaction."""
from psycopg.types.json import Jsonb
from tempo_intelligence.errors import DomainError


class RequestCommands:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def lock(self,command_id):
        self._database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("command:"+command_id,))

    def receipt(self,command_id,payload):
        previous=self._database.execute("SELECT * FROM commands WHERE command_id=%s",(command_id,)).fetchone()
        if previous:
            if previous["payload"] != payload:
                raise DomainError("command_replay_conflict")
        return previous

    def request(self,request_id,expected_revision):
        request=self._owner._head(self._database,request_id)
        if expected_revision is not None and expected_revision != request["revision"]:
            raise DomainError("request_revision_conflict")
        return request

    def locked_cache(self,cache_id):
        return self._database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(cache_id,)).fetchone()

    def queue_retry(self,cache_id):
        self._database.execute("UPDATE caches SET state='queued',attempts=0,error_code=NULL,next_attempt_at=NULL WHERE cache_id=%s",(cache_id,))

    def new_generation(self,cache):
        self._owner._new_generation(self._database,cache)

    def cache(self,cache_id):
        return self._database.execute("SELECT * FROM caches WHERE cache_id=%s",(cache_id,)).fetchone()

    def maia_job(self,current):
        self._owner._maia_job(self._database,current)

    def save(self,command_id,payload,result):
        self._database.execute("INSERT INTO commands VALUES(%s,%s,%s)",(command_id,Jsonb(payload),Jsonb(result)))
