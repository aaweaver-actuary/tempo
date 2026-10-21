"""HTTP readiness delegates storage access to its existing owner capability."""
from fastapi.testclient import TestClient
import pytest

from tempo_game_evidence.api import create_app


@pytest.mark.parametrize('unavailable', [False, True])
def test_health_uses_schema_access_capability_and_retains_failure_translation(unavailable):
    calls = []

    class Storage:
        def check_schema_access(self):
            calls.append('schema-access')
            if unavailable:
                raise RuntimeError('synthetic unavailable schema')

        def connect(self):
            raise AssertionError('The HTTP handler opened a database connection')

    response = TestClient(create_app(Storage())).get('/healthz')
    assert calls == ['schema-access']
    assert response.status_code == (503 if unavailable else 200)
    assert response.json() == ({'detail': 'game_evidence_database_unavailable'}
                               if unavailable else {'status': 'ready'})
