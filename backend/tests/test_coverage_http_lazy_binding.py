"""Coverage is selected only by its existing explicit operations."""
from app import main
from app.services import repertoire_coverage as owner


def test_coverage_forwarders_preserve_owner_signatures_and_results(monkeypatch):
    calls = []
    for name in ('claim_maia_coverage_node', 'coverage_gaps', 'coverage_summary',
                 'enqueue_coverage_refresh', 'set_explorer_session_token', 'submit_maia_coverage'):
        monkeypatch.setattr(owner, name, lambda *args, _name=name, **kwargs: calls.append((_name, args, kwargs)) or _name)
    assert main.claim_maia_coverage_node() == 'claim_maia_coverage_node'
    assert main.coverage_gaps('r') == 'coverage_gaps'
    assert main.coverage_summary('r') == 'coverage_summary'
    assert main.enqueue_coverage_refresh('r', automatic=True, background=True) == 'enqueue_coverage_refresh'
    assert main.set_explorer_session_token('token') == 'set_explorer_session_token'
    assert main.submit_maia_coverage('n', 'lease', [{'move': 'e4'}]) == 'submit_maia_coverage'
    assert calls == [
        ('claim_maia_coverage_node', (), {}), ('coverage_gaps', ('r',), {}),
        ('coverage_summary', ('r',), {}),
        ('enqueue_coverage_refresh', ('r',), {'automatic': True, 'background': True}),
        ('set_explorer_session_token', ('token',), {}),
        ('submit_maia_coverage', ('n', 'lease', [{'move': 'e4'}]), {}),
    ]


def test_postgres_coverage_commands_dispatch_without_owner(monkeypatch):
    from app import command_dispatch
    from app.contracts.transport.authored_integrity import CoverageMaiaSubmission
    calls = []
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda name, payload, **kw: calls.append((name, payload, kw)) or {'ok': True})
    for name in ('enqueue_coverage_refresh', 'claim_maia_coverage_node', 'submit_maia_coverage'):
        monkeypatch.setattr(main, name, lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('coverage owner invoked')))
    assert main.refresh_repertoire_coverage('r', 'refresh') == {'ok': True}
    assert main.coverage_maia_claim('claim') == {'ok': True}
    assert main.coverage_maia_submit(CoverageMaiaSubmission(node_id='n', lease_id='l', moves=[]), 'submit') == {'ok': True}
    assert calls == [
        ('coverage.refresh.request', {'repertoire_id': 'r'}, {'idempotency_key': 'refresh'}),
        ('coverage.maia.claim', {}, {'idempotency_key': 'claim', 'background': True}),
        ('coverage.maia.submit', {'node_id': 'n', 'lease_id': 'l', 'moves': []}, {'idempotency_key': 'submit', 'background': True}),
    ]
