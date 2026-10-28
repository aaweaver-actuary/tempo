"""SQL boundary proof only; fake driver is not PostgreSQL locking proof."""
from types import SimpleNamespace
import pytest
from sqlglot import exp, parse_one
from sqlglot.executor import execute
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
        assert "receipt.command_name = 'imports.pgn.explicit'" in parse_one(sql, read='postgres').sql(dialect='postgres')
        assert 'json_extract_path_text' not in sql.lower(), 'JSONB receipt SQL must stay native PostgreSQL'
        assert 'graph_generation' in sql and 'origin_graph_task_id' in sql
        assert 'graph_task_id' in sql and 'graph.generation' in sql
        assert 'background_activity' in sql and 'paused' in sql
        assert 'SKIP LOCKED' in sql
    storage.delivery(Database(), 'graph', 2, 'lease')
    assert calls[-1][1] == ('graph', 2, 'lease')
    assert "task.state='leased'" in calls[-1][0]


def _candidate_eligible(*, explicit, kind, study_only, current_import, paused, defensive_enabled):
    """Evaluate generated admission SQL over supplied facts, without a database.

    The receipt EXISTS is one supplied current-scope witness. Its native joins,
    locks and publication semantics belong to the separate native regression.
    All outer kind, Study-only, defensive and manual-pause predicates remain real.
    """
    statements = []
    class Database:
        def execute(self, statement, parameters):
            statements.append(statement)
            return SimpleNamespace(fetchone=lambda: None)
        execute_native = execute
    _, candidate, _ = task_storage.prepare_task_claim(
        None, None, 'now', ('defensive_admission',), explicit_imports_only=explicit)
    candidate(Database(), ['now'])
    where = parse_one(statements[0], read='postgres' if explicit else 'sqlite').args['where'].this

    def supplied_facts(node):
        if isinstance(node, exp.Exists):
            assert 'operation_receipts' in {table.name for table in node.find_all(exp.Table)}
            return exp.convert(current_import)
        if isinstance(node, exp.Subquery):
            field = node.this.expressions[0].name
            return exp.convert({'paused': paused, 'study_only_mode': study_only,
                                'defensive_analysis_enabled': defensive_enabled}[field])
        if isinstance(node, exp.Column):
            return exp.convert({'kind': kind, 'state': 'queued', 'next_attempt_at': 0}[node.name])
        if isinstance(node, (exp.Placeholder, exp.Parameter)):
            return exp.convert(1)  # The queued candidate is due.
        return node

    return execute(exp.select(where.transform(supplied_facts))).rows[0][0]


@pytest.mark.parametrize('explicit,kind,study_only,current_import,paused,defensive_enabled,expected', [
    (True, 'opening_graph_rebuild', 1, True, 0, 0, True),
    (True, 'integrity_scan', 1, True, 0, 0, True),
    (True, 'opening_graph_rebuild', 0, True, 0, 0, True),
    (True, 'opening_graph_rebuild', 1, False, 0, 0, False),
    (True, 'integrity_scan', 1, False, 0, 0, False),
    (True, 'opening_graph_rebuild', 1, True, 1, 0, False),
    (True, 'daily_queue', 1, True, 0, 0, False),
    (True, 'defensive_admission', 0, True, 0, 1, False),
    (True, 'game_analysis_publish', 1, True, 0, 0, False),
    (False, 'opening_graph_rebuild', 1, True, 0, 0, False),
    (False, 'integrity_scan', 1, True, 0, 0, False),
    (False, 'game_analysis_publish', 1, False, 0, 0, False),
    (False, 'daily_queue', 1, False, 0, 0, True),
    (False, 'opening_graph_rebuild', 0, False, 0, 0, True),
    (False, 'daily_queue', 1, False, 1, 0, False),
    (False, 'defensive_admission', 0, False, 0, 0, False),
    (False, 'defensive_admission', 0, False, 0, 1, True),
])
def test_explicit_import_claim_policy_keeps_the_exception_receipt_scoped(
    explicit, kind, study_only, current_import, paused, defensive_enabled, expected,
):
    assert _candidate_eligible(explicit=explicit, kind=kind, study_only=study_only,
        current_import=current_import, paused=paused, defensive_enabled=defensive_enabled) is expected
