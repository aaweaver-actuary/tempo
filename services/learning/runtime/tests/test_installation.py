"""Installed closure and infrastructure-free denial; also run after producer removal."""

from importlib import metadata
from pathlib import Path
import json, subprocess, sys
import pytest
import tempo_learning_runtime


def test_installed_artifact_has_migrations_and_no_optional_services():
    assert "site-packages" in tempo_learning_runtime.__file__
    assert metadata.version("tempo-learning-runtime") == "0.1.0"
    for distribution in ("celery", "redis", "torch", "onnxruntime", "transformers"):
        with pytest.raises(metadata.PackageNotFoundError):
            metadata.distribution(distribution)
    package = Path(tempo_learning_runtime.__file__).parent
    assert (package / "migrations/manifest.json").is_file()
    assert (
        json.loads((package / "fixtures/legacy-protocol.json").read_text())["protocol"]
        == "legacy-learning-http"
    )
    from tempo_learning_runtime.postgres_store import PostgresConnection

    assert not hasattr(PostgresConnection, "commit") and not hasattr(
        PostgresConnection, "rollback"
    )


def test_import_all_deferred_paths_and_denied_auth_need_no_infrastructure():
    guard = r"""
import sys, pkgutil
from importlib.abc import MetaPathFinder
class RejectOptional(MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'app','celery','redis','torch','onnxruntime','transformers','sqlite3'}:
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
