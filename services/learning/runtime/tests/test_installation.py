"""Installed closure and infrastructure-free denial; also run after producer removal."""

from importlib import metadata
from pathlib import Path
import json, subprocess, sys
import pytest
import tempo_learning_runtime
import tempo_learning_database


def test_installed_artifact_has_migrations_and_no_optional_services():
    assert "site-packages" in tempo_learning_runtime.__file__
    assert metadata.version("tempo-learning-runtime") == "0.1.0"
    assert metadata.version("tempo-game-evidence-contracts") == "2.0.0"
    for distribution in ("celery", "redis", "torch", "onnxruntime", "transformers"):
        with pytest.raises(metadata.PackageNotFoundError):
            metadata.distribution(distribution)
    package = Path(tempo_learning_database.__file__).parent
    assert (package / "ddl/migrations/manifest.json").is_file()
    migrations = json.loads((package / "ddl/migrations/manifest.json").read_text())
    assert [entry["id"] for entry in migrations] == [
        "learning.runtime/0001",
        "learning.runtime/0002",
        "learning.remediation/0001",
        "learning.bridge/0001",
    ]
    assert migrations[1]["depends_on"] == ["learning.runtime/0001"]
    assert all(
        __import__("tempo_learning_database.ddl.migrate", fromlist=["migration_source"]).migration_source(entry)
        for entry in migrations
    )
    assert (
        json.loads((Path(tempo_learning_runtime.__file__).parent / "fixtures/legacy-protocol.json").read_text())["protocol"]
        == "legacy-learning-http"
    )
    from tempo_learning_database.adapters.postgres_store import PostgresConnection

    assert not hasattr(PostgresConnection, "commit") and not hasattr(
        PostgresConnection, "rollback"
    )


def test_import_all_deferred_paths_and_denied_auth_need_no_infrastructure():
    guard = r"""
import sys, pkgutil
from importlib.abc import MetaPathFinder
class RejectOptional(MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'app','celery','redis','torch','onnxruntime','transformers','sqlite3','tempo_game_evidence'}:
            raise AssertionError('Forbidden runtime dependency: '+fullname)
sys.meta_path.insert(0,RejectOptional())
def refuse_connection(event,arguments):
    if event in {'socket.connect','sqlite3.connect'}:raise AssertionError('Unexpected infrastructure: '+event)
sys.addaudithook(refuse_connection)
import tempo_learning_runtime as learning
for module in pkgutil.walk_packages(learning.__path__, learning.__name__+'.'):
    __import__(module.name)
from fastapi.testclient import TestClient
configuration=learning.Configuration('w','postgresql://unused/learning','postgresql://reader/learning','x'*40,maintenance=False,tablebase_url=None)
with TestClient(learning.create_app(configuration)) as client:
    assert client.get('/health/live').status_code==200
    assert client.get('/api/queue/window').status_code==401
print('installed deferred imports and denied authentication passed')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", guard], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_installed_receipt_policy_replays_stale_identity_without_content_or_writes():
    """Installed API compatibility; actual storage/races live in native suites."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import hashlib
    from tempo_game_evidence_contracts.v1 import EvidenceDelivery
    from tempo_learning_remediation import PostgresRepository, ReceiveContext

    value = EvidenceDelivery.model_validate({
        'contract_version': 1, 'workspace_id': 'installed', 'delivery_id': 'stale',
        'evidence_id': 'source', 'evidence_revision': 4, 'game_id': 'game', 'game_revision': 4,
        'encounters': [{'encounter_id': 'encounter', 'ply': 0, 'position_key': 'position',
                        'decision_id': 'decision', 'observed_move': 'd2d4'}],
        'comparisons': [], 'misses': [], 'obligations': [],
    })
    response = {'contract_version': 1, 'workspace_id': 'installed', 'delivery_id': 'stale',
        'evidence_id': 'source', 'evidence_revision': 4, 'status': 'stale_revision',
        'accepted_obligation_ids': [], 'learning_revision': None, 'reason': 'source_revision_superseded'}
    payload_hash = hashlib.sha256(json.dumps(value.model_dump(mode='json'), sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    class ReceiptReader:
        info = SimpleNamespace(transaction_status=SimpleNamespace(name='INTRANS'))
        def execute(self, statement, parameters=()):
            if statement.startswith('SELECT pg_advisory_xact_lock'):
                row = None
            elif statement.startswith('SELECT payload_hash,response_json'):
                row = (payload_hash, response)
            elif statement.startswith('SELECT response_json'):
                row = (response,)
            else:
                raise AssertionError('Replay attempted content lookup or a write: ' + statement)
            return SimpleNamespace(fetchone=lambda: row)
    context = ReceiveContext('installed', 'game', 4, 'source', 4, datetime.now(timezone.utc), None)
    owned = PostgresRepository(ReceiptReader(), 'installed')
    assert owned.receive(value, context, historical_evidence_revision=5, v1_transport_stale=True).model_dump(mode='json') == response
    assert owned.record_status(value, 'retryable_failure', 'temporary').model_dump(mode='json') == response
    changed = value.model_copy(update={'game_revision': 6})
    assert owned.receive(changed, context).status == 'conflict'
