"""SQL boundary proof only; fake driver is not PostgreSQL locking proof."""
from types import SimpleNamespace
from tempo_legacy_database.adapters.authored_repertoires.pgn_import import preparation as storage
from tempo_legacy_database.adapters.durable_tasks import storage as task_storage


def test_explicit_claim_keeps_receipt_generation_manual_pause_and_lease_fences():
    calls = []
    class Database:
        def execute_native(self, sql, parameters):
            calls.append((sql, parameters))
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
    reclaim, candidate, parameters = task_storage.prepare_task_claim(None, None, 'now', (), explicit_imports_only=True)
    reclaim(Database())
    candidate(Database(), ['now', *parameters])
    for sql, _ in calls:
        assert "receipt.command_name = 'imports.pgn.explicit'" in sql
        assert 'graph_generation' in sql and 'origin_graph_task_id' in sql
        assert 'graph_task_id' in sql and 'graph.generation' in sql
        assert 'background_activity' in sql and 'paused' in sql
        assert 'SKIP LOCKED' in sql
    storage.delivery(Database(), 'graph', 2, 'lease')
    assert calls[-1][1] == ('graph', 2, 'lease')
    assert "task.state='leased'" in calls[-1][0]
