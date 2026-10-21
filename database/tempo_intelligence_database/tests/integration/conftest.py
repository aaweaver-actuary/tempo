import json
import os
from pathlib import Path
import socket

import psycopg
import pytest

from tempo_intelligence.config import Configuration
from tempo_intelligence_database.adapters.store import Store
from .support import FIXTURES


@pytest.fixture(autouse=True)
def local_network_only(monkeypatch):
    original=socket.socket.connect
    def connect(connection,address):
        if isinstance(address,tuple) and address[0] not in ("127.0.0.1","::1"):
            raise AssertionError("external_network_disabled")
        return original(connection,address)
    monkeypatch.setattr(socket.socket,"connect",connect)


@pytest.fixture
def state(tmp_path):
    assert os.environ.get("TEMPO_INTELLIGENCE_TEST_CONTAINER","").startswith("tempo-intelligence-109-")
    with psycopg.connect(os.environ["TEMPO_INTELLIGENCE_TEST_OWNER_URL"]) as database:
        database.execute("TRUNCATE intelligence.analysis_execution_events,intelligence.analysis_inputs,intelligence.analysis_pages,intelligence.analysis_runs,intelligence.analysis_heads,intelligence.analysis_positions,intelligence.analysis_executions,intelligence.analysis_results,intelligence.analysis_reports,intelligence.analysis_report_items,intelligence.analysis_dismissals,intelligence.analysis_commands,intelligence.snapshots,intelligence.requests,intelligence.request_heads,intelligence.caches,intelligence.evidence,intelligence.commands,intelligence.maia_jobs,intelligence.model_profiles,intelligence.credential_head,intelligence.credential_registrations,intelligence.provider_control CASCADE")
        database.execute("INSERT INTO intelligence.credential_head(id) VALUES(1)")
        database.execute("INSERT INTO intelligence.provider_control(id) VALUES(1)")
    request=json.loads((FIXTURES/"request-v1.json").read_text())
    source=next(source for source in request["sources"] if source["kind"]=="maia")
    path=tmp_path.resolve()/"provider.json"
    path.parent.chmod(0o700)
    configuration=Configuration(workspace_id="fixture",database_url=os.environ["TEMPO_INTELLIGENCE_TEST_RUNTIME_URL"],
        caller_token="caller-"+"c"*40,operator_token="operator-"+"o"*40,worker_token="worker-"+"w"*40,
        credential_file=path,worker_enabled=False,model_profiles=({"model_id":source["model_id"],"model_version":source["model_version"],"artifact_sha256":source["artifact_sha256"],"ratings":[source["rating"]]},))
    store=Store(configuration)
    store.validate_startup()
    try:
        yield store,configuration,path
    finally:
        store.close()
