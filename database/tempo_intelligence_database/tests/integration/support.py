"""Only synthetic inputs for the invocation-owned PostgreSQL fixture."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

import psycopg

from tempo_intelligence.credentials import PrivateCredentials

PROJECT=Path(__file__).resolve().parents[4]
SERVICE=PROJECT/"services/intelligence" if (PROJECT/"services/intelligence").is_dir() else PROJECT
FIXTURES=SERVICE/"fixtures"


def request_value(sources=None,request_id="request"):
    payload=json.loads((FIXTURES/"request-v1.json").read_text())
    payload["request_id"]=request_id
    payload["sources"]=sources if sources is not None else [{"kind":"lichess"}]
    return payload


def owner_execute(query,parameters=()):
    with psycopg.connect(os.environ["TEMPO_INTELLIGENCE_TEST_OWNER_URL"]) as database:
        return database.execute(query,parameters).fetchall() if query.lstrip().upper().startswith("SELECT") else database.execute(query,parameters)


def ready(store):
    owner_execute("UPDATE intelligence.provider_control SET next_attempt_at=NULL,lease_token=NULL,leased_until=NULL")
    owner_execute("UPDATE intelligence.caches SET next_attempt_at=NULL WHERE state IN ('queued','retry_wait','waiting_credentials')")


def register(state,token="test-provider-token"):
    store,configuration,path=state
    payload={"schema_version":1,"registration_id":str(uuid.uuid4()),"token":token}
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    material=PrivateCredentials(path).load()
    store.credential_reload(material,store.credential_status()["generation"])
    return material


def maia_result(job):
    now=datetime.now(timezone.utc).isoformat()
    return {"contract_version":1,"workspace_id":job["workspace_id"],"job_id":job["job_id"],
        "request_revision":job["request_revision"],"execution_generation":job["execution_generation"],"worker":"maia",
        "model_id":job["model_id"],"model_version":job["model_version"],
        "provenance":{"input_digest":job["input_digest"],"runtime_name":"fixture","runtime_version":"1","implementation_version":"1","started_at":now,"finished_at":now},
        "predictions":[{"move":move,"probability":1.0 if index==0 else 0.0} for index,move in enumerate(job["legal_moves"])]}
