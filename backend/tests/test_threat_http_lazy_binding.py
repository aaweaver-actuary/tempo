"""The legacy HTTP surface selects its threat owner only when called."""
from app import main
from app.services import threat_pipeline as owner


def test_threat_forwarders_preserve_owner_calls_and_results(monkeypatch):
    calls = []
    names = ('claim_analysis_request', 'enqueue_candidate_validation', 'enqueue_threat_scan',
             'enqueue_threat_backfill', 'execute_threat_backfill_slice', 'execute_threat_report_audit',
             'execute_threat_scan_slice', 'execute_threat_validation', 'save_analysis_report')
    for name in names:
        monkeypatch.setattr(owner, name, lambda *args, _name=name, **kwargs: calls.append((_name, args, kwargs)) or _name)
    assert main.claim_analysis_request() == names[0]
    assert main.enqueue_candidate_validation('c', background=True) == names[1]
    assert main.enqueue_threat_scan('g', 4, background=False) == names[2]
    assert main.enqueue_threat_backfill() == names[3]
    for name in names[4:8]:
        assert getattr(main, name)({'id': 't'}) == name
    assert main.save_analysis_report('r', 'l', {'report': 1}) == names[8]
    assert calls == [
        (names[0], (), {}), (names[1], ('c',), {'background': True}),
        (names[2], ('g', 4), {'background': False}), (names[3], (), {}),
        *((name, ({'id': 't'},), {}) for name in names[4:8]),
        (names[8], ('r', 'l', {'report': 1}), {}),
    ]


def test_postgres_threat_routes_dispatch_without_owner(monkeypatch):
    from app import command_dispatch
    from app.contracts.transport.defense import ThreatAnalysisSubmission
    calls = []
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda name, payload, **kw: calls.append((name, payload, kw)) or {'ok': True})
    for name in ('claim_analysis_request', 'enqueue_threat_backfill', 'save_analysis_report'):
        monkeypatch.setattr(main, name, lambda *a, **kw: (_ for _ in ()).throw(AssertionError('owner invoked')))
    assert main.claim_defensive_threat_analysis('docker', 'claim') == {'ok': True}
    assert main.backfill_defensive_threats('backfill') == {'ok': True}
    assert main.submit_defensive_threat_analysis('r', ThreatAnalysisSubmission(lease_id='l', report={}), 'report') == {'ok': True}
    assert calls == [
        ('threat.analysis.claim', {}, {'idempotency_key': 'claim', 'background': True}),
        ('defensive.backfill', {}, {'idempotency_key': 'backfill'}),
        ('threat.analysis.report', {'request_id': 'r', 'lease_id': 'l', 'report': {}, 'diagnostics': None}, {'idempotency_key': 'report', 'background': True}),
    ]
