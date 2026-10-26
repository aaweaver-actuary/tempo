"""V2 raw admission and normalized expansion share one bounded receiver limit."""
from contextlib import contextmanager
import json

from fastapi.testclient import TestClient
import pytest

from tempo_learning_runtime import api, publication_bridge
from tempo_learning_runtime.config import Configuration
from tempo_learning_runtime.publications import PublicationBundle
from test_decision_projection import projected_bundle


@pytest.mark.parametrize('authorized,status', [(False, 401), (True, 413)])
def test_v2_raw_stream_stops_over_16_mib_before_json_model_or_backend(monkeypatch, authorized, status):
    events = []
    class NoBackend:
        @contextmanager
        def scope(self):
            pytest.fail('Oversized or unauthorized input reached database scope')
            yield
        def close(self):
            pass
    async def no_json(self):
        pytest.fail('Oversized or unauthorized input reached JSON/model parsing')
    monkeypatch.setattr(api, 'Database', lambda _: NoBackend())
    monkeypatch.setattr(api, 'register_handlers', lambda: None)
    monkeypatch.setattr(api.Request, 'json', no_json)
    configuration = Configuration('limit-test', 'unused', 'unused', 'u' * 40,
                                  operator_token='o' * 40, maintenance=False, host_activity_directory=None)
    app = api.create_app(configuration)
    def chunks():
        for _ in range(16):
            events.append('chunk')
            yield b' ' * (1024 * 1024)
        events.append('over limit')
        yield b' '
    headers = {'Content-Type': 'application/json'}
    if authorized:
        headers['Authorization'] = 'Bearer ' + configuration.operator_token
    with TestClient(app) as client:
        response = client.post('/internal/publications/v2/stage', content=chunks(), headers=headers)
    assert response.status_code == status
    if authorized:
        assert response.json() == {'detail': 'Publication bundle exceeds 16 MiB'}
        assert events[-1] == 'over limit'
    else:
        assert events == []


def test_v2_normalized_expansion_remains_bounded_when_compact_raw_body_fits(monkeypatch):
    payload = projected_bundle()
    compact = json.dumps(payload, separators=(',', ':')).encode()
    normalized = publication_bridge.PublicationBundleV2.model_validate(payload)
    expanded_bytes = len(normalized.model_dump_json().encode())
    assert len(compact) < expanded_bytes
    monkeypatch.setattr(publication_bridge, 'MAX_BUNDLE_BYTES', expanded_bytes)
    assert publication_bridge.PublicationBundleV2.model_validate(payload).publication == normalized.publication
    monkeypatch.setattr(publication_bridge, 'MAX_BUNDLE_BYTES', expanded_bytes - 1)
    assert len(compact) < publication_bridge.MAX_BUNDLE_BYTES
    with pytest.raises(ValueError, match='Publication bundle exceeds'):
        publication_bridge.PublicationBundleV2.model_validate(payload)


def test_v2_receiver_change_keeps_v1_definition_v2_count_and_alias_limits():
    assert publication_bridge.MAX_BUNDLE_BYTES == 16_777_216
    assert PublicationBundle.model_json_schema()['properties']['facts']['maxItems'] == 128
    schema = publication_bridge.PublicationBundleV2.model_json_schema()
    assert schema['properties']['facts']['maxItems'] == publication_bridge.MAX_DEFINITIONS == 10000
    assert schema['properties']['legacy_aliases']['maxProperties'] == publication_bridge.MAX_LEGACY_ALIASES == 8192
    assert schema['properties']['routes']['maxItems'] == 10000
