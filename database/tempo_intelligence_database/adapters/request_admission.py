"""Fixed request-admission operations and replay guards on the owner's write."""
from psycopg.types.json import Jsonb

from tempo_intelligence.errors import DomainError


class RequestAdmission:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def lock(self,request):
        self._database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("request:"+request.request_id,))

    def previous(self,request):
        return self._database.execute("SELECT payload FROM requests WHERE request_id=%s AND revision=%s",(request.request_id,request.request_revision)).fetchone()

    def verify_replay(self,old,payload):
        if old["payload"] != payload:
            raise DomainError("request_replay_conflict")

    def head(self,request):
        return self._database.execute("SELECT revision FROM request_heads WHERE request_id=%s",(request.request_id,)).fetchone()

    def snapshot(self,snapshot_identity,immutable_snapshot):
        self._database.execute("INSERT INTO snapshots VALUES(%s,%s) ON CONFLICT DO NOTHING",(snapshot_identity,Jsonb(immutable_snapshot)))
        existing_snapshot=self._database.execute("SELECT payload FROM snapshots WHERE identity=%s",(snapshot_identity,)).fetchone()
        if existing_snapshot["payload"] != immutable_snapshot:
            raise DomainError("snapshot_replay_conflict")

    def profile(self,descriptor):
        self._owner._profile(self._database,descriptor)

    def initialize_cache(self,cache_id,descriptor,snapshot,state):
        self._database.execute("INSERT INTO caches(cache_id,descriptor,input_snapshot,kind,state) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(cache_id,Jsonb(descriptor),Jsonb(snapshot),descriptor["kind"],state))

    def locked_cache(self,cache_id):
        return self._database.execute("SELECT * FROM caches WHERE cache_id=%s FOR UPDATE",(cache_id,)).fetchone()

    def accepted(self,cache):
        return self._database.execute("SELECT accepted_at FROM evidence WHERE evidence_id=%s",(cache["accepted_id"],)).fetchone()

    def new_generation(self,cache):
        self._owner._new_generation(self._database,cache)

    def cache(self,cache_id):
        return self._database.execute("SELECT * FROM caches WHERE cache_id=%s",(cache_id,)).fetchone()

    def maia_job(self,cache):
        self._owner._maia_job(self._database,cache)

    def save(self,request,payload,bindings):
        self._database.execute("INSERT INTO requests(request_id,revision,payload,bindings) VALUES(%s,%s,%s,%s)",(request.request_id,request.request_revision,Jsonb(payload),Jsonb(bindings)))
        self._database.execute("INSERT INTO request_heads VALUES(%s,%s) ON CONFLICT(request_id) DO UPDATE SET revision=excluded.revision",(request.request_id,request.request_revision))
