"""Installed closure and infrastructure-free denial; also run after producer removal."""

from importlib import metadata
from pathlib import Path
import hashlib, json, subprocess, sys
import pytest
import tempo_learning_runtime
import tempo_learning_database


def test_installed_artifact_has_migrations_and_no_optional_services():
    assert "site-packages" in tempo_learning_runtime.__file__
    assert metadata.version("tempo-learning-runtime") == "0.1.0"
    assert metadata.version("tempo-evidence-math") == "0.1.0"
    import tempo_evidence_math
    from tempo_learning_runtime import position_grade_policy
    assert "site-packages" in tempo_evidence_math.__file__
    assert "site-packages" in position_grade_policy.__file__
    assert metadata.version("tempo-game-evidence-contracts") == "3.0.0"
    for distribution in ("celery", "redis", "torch", "onnxruntime", "transformers"):
        with pytest.raises(metadata.PackageNotFoundError):
            metadata.distribution(distribution)
    package = Path(tempo_learning_database.__file__).parent
    assert (package / "ddl/migrations/manifest.json").is_file()
    migrations = json.loads((package / "ddl/migrations/manifest.json").read_text())
    expected = [
        ("learning.runtime/0001", "#224/#194", [], "0dc465b03c6cd0e2487744bdbb58d9a21d1db8e4aa4037323295fe507d0df26e"),
        ("learning.runtime/0002", "#224/#194", ["learning.runtime/0001"], "c3a1b03b8f4aad7e026be8a45eaf4ccd6a567ce5da23c9c42390ae6d623ef4ab"),
        ("learning.remediation/0001", "#224/F1", ["learning.runtime/0002"], "96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d"),
        ("learning.bridge/0001", "#193/A", ["learning.remediation/0001"], "c4cde173083334cee565a125daa79e0e111d3b0498c87ca9151a61ac3679be91"),
        ("learning.retained_position/0001", "#908/W02A", ["learning.bridge/0001"], "7db5bc140c4f7b3de29df24a57f298fa931792e438a468097c80f0ebf4ebbfc0"),
        ("learning.queue_epochs/0001", "#908/W03A", ["learning.retained_position/0001"], "29c7370a3287c9b8b765987221ef8723be8137baec86a907304be98e5af25061"),
    ]
    from tempo_learning_database.ddl.migrate import migration_source
    assert [entry["id"] for entry in migrations[:len(expected)]] == [row[0] for row in expected]
    seen = set()
    for index, entry in enumerate(migrations):
        assert entry["id"] not in seen
        assert isinstance(entry["owner"], str) and entry["owner"].strip()
        assert isinstance(entry["depends_on"], list)
        assert set(entry["depends_on"]) <= seen
        source = migration_source(entry)
        assert source
        if index < len(expected):
            _, owner, dependencies, checksum = expected[index]
            assert entry["owner"] == owner
            assert entry["depends_on"] == dependencies
            assert hashlib.sha256(source).hexdigest() == checksum
        seen.add(entry["id"])
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
