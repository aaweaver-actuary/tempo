"""Fixed request-inspection reads on the Intelligence owner's transaction."""


class RequestInspection:
    def __init__(self,owner,database):
        self._owner=owner
        self._database=database

    def request(self,request_id):
        return self._owner._head(self._database,request_id)

    def cache(self,cache_id):
        return self._database.execute("SELECT * FROM caches WHERE cache_id=%s",(cache_id,)).fetchone()

    def evidence(self,evidence_id):
        return self._database.execute("SELECT accepted_at FROM evidence WHERE evidence_id=%s",(evidence_id,)).fetchone()

    def credential_status(self):
        return self._owner._credential_status(self._database)

    def provider_control(self):
        return self._database.execute("SELECT next_attempt_at FROM provider_control WHERE id=1").fetchone()
