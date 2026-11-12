"""Completed reviews survive mutable daily-queue projections without fabricated origins."""


def test_queue_origin_migration_follows_current_main_without_renumbering_published_versions():
    from pathlib import Path
    from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION

    root = Path(__file__).resolve().parents[2] / "database/tempo_storage/ddl"
    migrations = sorted((root / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    assert [int(path.name[:3]) for path in migrations] == list(
        range(1, POSTGRES_SCHEMA_VERSION + 1)
    )
    assert (root / "migrations/030_opening_decision_evidence.sql").is_file()
    assert (root / "migrations/031_defensive_analysis_pause.sql").is_file()
    queue_migration = root / "migrations/032_queue_attempt_origins.sql"
    assert (
        "INSERT INTO tempo_schema_migrations(version) VALUES (32);"
        in queue_migration.read_text()
    )
    assert POSTGRES_SCHEMA_VERSION >= 39
    assert (root / "migrations/037_card_deletion.sql").is_file()
    assert (root / "migrations/038_prefix_transition_application.sql").is_file()
    assert (root / "migrations/039_prefix_transition_lock_budget.sql").is_file()


def _queue_selector_namespace():
    """Load only inert selection declarations, not product imports or fixtures."""
    import ast
    import json
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "database/tests/check_postgres_queue_attempt_recovery.py"
    tree = ast.parse(source.read_text())
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id.startswith("_QUEUE_RECOVERY_")
                    for target in node.targets))
        or (isinstance(node, ast.FunctionDef) and node.name == "queue_recovery_main")
    ]
    namespace = {"json": json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    events = []
    for name in namespace["_QUEUE_RECOVERY_ALL"]:
        namespace[name] = lambda name=name: events.append(name)
    return namespace, events


def test_queue_recovery_selector_preserves_default_eight_and_exact_maintained_six(capsys):
    import json

    namespace, events = _queue_selector_namespace()
    all_cases = namespace["_QUEUE_RECOVERY_ALL"]
    maintained = namespace["_QUEUE_RECOVERY_MAINTAINED_SIX"]
    unresolved = namespace["_QUEUE_RECOVERY_UNRESOLVED"]
    assert len(all_cases) == 8 and len(maintained) == 6 and len(unresolved) == 2
    assert maintained == tuple(case for case in all_cases if case not in unresolved)
    namespace["queue_recovery_main"]([])
    assert events == list(all_cases)
    events.clear()
    capsys.readouterr()
    namespace["queue_recovery_main"](["--case-set", "maintained-six"])
    assert events == list(maintained)
    receipts = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [receipt["execution"] for receipt in receipts] == ["starting", "completed"]
    assert receipts[-1]["scope"] == "partial"
    assert receipts[-1]["executed_count"] == 6
    assert receipts[-1]["excluded_cases"] == list(unresolved)
    assert receipts[-1]["unresolved_owner_cases"] == list(unresolved)


def test_queue_recovery_selector_discovery_does_not_execute_or_claim_completion(capsys):
    import json

    namespace, events = _queue_selector_namespace()
    for args in (["--list"], ["--case-set", "maintained-six", "--list"]):
        namespace["queue_recovery_main"](args)
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["execution"] == "discovery-only"
        assert "executed_count" not in receipt
    assert events == []


def test_queue_recovery_selector_rejects_arbitrary_and_empty_selections():
    import pytest

    namespace, events = _queue_selector_namespace()
    for args in (["--case-set", "arbitrary"], ["--case-set", namespace["_QUEUE_RECOVERY_ALL"][0]],
                 ["--case", "test_any"], ["unexpected"]):
        with pytest.raises(SystemExit) as error:
            namespace["queue_recovery_main"](args)
        assert error.value.code == 2
    assert events == []
    namespace["_QUEUE_RECOVERY_MAINTAINED_SIX"] = ()
    with pytest.raises(RuntimeError, match="nonempty"):
        namespace["queue_recovery_main"](["--case-set", "maintained-six"])


def test_queue_recovery_selector_propagates_failure_without_completed_receipt(capsys):
    import pytest

    namespace, events = _queue_selector_namespace()
    cases = namespace["_QUEUE_RECOVERY_MAINTAINED_SIX"]
    def failure():
        events.append(cases[1])
        raise RuntimeError("original native failure")
    namespace[cases[1]] = failure
    with pytest.raises(RuntimeError, match="original native failure"):
        namespace["queue_recovery_main"](["--case-set", "maintained-six"])
    assert events == list(cases[:2])
    assert '"execution": "completed"' not in capsys.readouterr().out
