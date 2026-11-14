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

    source = (
        Path(__file__).resolve().parents[2]
        / "database/tests/check_postgres_queue_attempt_recovery.py"
    )
    tree = ast.parse(source.read_text())
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id.startswith("_QUEUE_RECOVERY_")
                for target in node.targets
            )
        )
        or (isinstance(node, ast.FunctionDef) and node.name == "queue_recovery_main")
    ]
    namespace = {"json": json}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace
    )
    events = []
    for name in namespace["_QUEUE_RECOVERY_ALL"]:
        namespace[name] = lambda name=name: events.append(name)
    return namespace, events


def test_queue_recovery_selector_preserves_default_eight_and_exact_maintained_six(
    capsys,
):
    import json

    namespace, events = _queue_selector_namespace()
    all_cases = namespace["_QUEUE_RECOVERY_ALL"]
    maintained = namespace["_QUEUE_RECOVERY_MAINTAINED_SIX"]
    unresolved = namespace["_QUEUE_RECOVERY_UNRESOLVED"]
    excluded = namespace["_QUEUE_RECOVERY_EXCLUDED"]
    assert len(all_cases) == 8 and len(maintained) == 6 and len(unresolved) == 1
    assert maintained == tuple(case for case in all_cases if case not in excluded)
    assert excluded == (
        "test_postgres_paused_feedback_history_preserves_ordinary_study",
        *unresolved,
    )
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
    assert receipts[-1]["excluded_cases"] == list(excluded)
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
    for args in (
        ["--case-set", "arbitrary"],
        ["--case-set", namespace["_QUEUE_RECOVERY_ALL"][0]],
        ["--case", "test_any"],
        ["unexpected"],
    ):
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


def _queue_environment_helpers():
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "database/tests/check_postgres_repertoire_limits.py"
    )
    tree = ast.parse(source.read_text())
    names = {"snapshot_queue_environment", "restore_queue_environment"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(nodes) == 2
    namespace = {}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace
    )
    return namespace


def test_queue_environment_snapshot_restore_preserves_native_parameters_and_history():
    helpers = _queue_environment_helpers()
    task = {"id": "original-task", "generation": 17}
    events = [{"id": 91, "task_id": "original-task", "message": "original"}]
    projections = [{"queue_date": "2099-01-01", "generation": 4}]
    metrics = [{"kind": "daily_queue", "shard": 2, "slot": 3, "count": 5}]
    cards = [
        {"id": "first", "state": "learning", "introduced_at": "2098-01-01"},
        {"id": "second", "state": "learning", "introduced_at": "2098-02-01"},
    ]
    epochs = [
        {"repertoire_id": "existing", "version": 12},
        {"repertoire_id": "absent", "version": None},
    ]

    class Database:
        def __init__(self):
            self.calls = []
            self.results = [task, events, projections, metrics, cards, epochs]

        def execute(self, statement, parameters=()):
            assert "?" not in statement
            assert statement.count("%s") == len(parameters)
            self.calls.append((statement, tuple(parameters)))
            result = self.results.pop(0) if self.results else None
            return type(
                "Cursor",
                (),
                {"fetchone": lambda _: result, "fetchall": lambda _: result},
            )()

    database = Database()
    snapshot = helpers["snapshot_queue_environment"](
        database, ("2099-01-01", "2099-01-02")
    )
    assert snapshot == {
        "queue_dates": ("2099-01-01", "2099-01-02"),
        "task": task,
        "events": events,
        "projections": projections,
        "metrics": metrics,
        "stale_introductions": cards,
        "priority_epochs": epochs,
    }
    assert database.calls[1][1] == ("original-task",)
    assert database.calls[-1][1] == ("first", "second", "first", "second")
    database.calls.clear()
    helpers["restore_queue_environment"](database, snapshot)
    inserts = [
        (sql, params) for sql, params in database.calls if sql.startswith("INSERT")
    ]
    for table, row in (
        ("background_tasks", task),
        ("background_task_events", events[0]),
        ("queue_projections", projections[0]),
        ("background_metric_buckets", metrics[0]),
    ):
        assert (
            f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('%s' for _ in row)})",
            tuple(row.values()),
        ) in inserts
    assert (
        "INSERT INTO priority_repertoire_source_epochs(repertoire_id,version) VALUES(%s,%s)",
        ("existing", 12),
    ) in inserts
    assert not any(params[0] == "absent" for _, params in inserts)
    assert (
        "DELETE FROM priority_repertoire_source_epochs WHERE repertoire_id=%s",
        ("absent",),
    ) in database.calls
    update_index = max(
        i for i, (sql, _) in enumerate(database.calls) if sql.startswith("UPDATE cards")
    )
    epoch_index = min(
        i
        for i, (sql, _) in enumerate(database.calls)
        if "priority_repertoire_source_epochs" in sql
    )
    assert update_index < epoch_index


def test_queue_environment_absent_task_and_empty_cards_do_not_invent_history():
    helpers = _queue_environment_helpers()

    class Database:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=()):
            assert "?" not in statement
            assert statement.count("%s") == len(parameters)
            self.calls.append((statement, parameters))
            return type(
                "Cursor", (), {"fetchone": lambda _: None, "fetchall": lambda _: []}
            )()

    database = Database()
    snapshot = helpers["snapshot_queue_environment"](
        database, ("2099-01-01", "2099-01-01")
    )
    assert snapshot["task"] is None
    assert (
        snapshot["events"]
        == snapshot["priority_epochs"]
        == snapshot["stale_introductions"]
        == []
    )
    assert not any(
        "background_task_events" in sql or "priority_repertoire_source_epochs" in sql
        for sql, _ in database.calls
    )
    database.calls.clear()
    helpers["restore_queue_environment"](database, snapshot)
    assert len(database.calls) == 3
    assert all(sql.startswith("DELETE") for sql, _ in database.calls)


def test_maintained_queue_cases_and_fixture_use_native_sql_markers():
    import ast
    from pathlib import Path

    namespace, _ = _queue_selector_namespace()
    selected = set(namespace["_QUEUE_RECOVERY_MAINTAINED_SIX"]) | {
        "_scheduling_adapter_fixture"
    }
    source = (
        Path(__file__).resolve().parents[2]
        / "database/tests/check_postgres_queue_attempt_recovery.py"
    )
    tree = ast.parse(source.read_text())
    checked = 0
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name not in selected:
            continue
        for call in ast.walk(function):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("execute", "execute_native")
                and call.args
            ):
                for literal in ast.walk(call.args[0]):
                    if isinstance(literal, ast.Constant) and isinstance(
                        literal.value, str
                    ):
                        assert "?" not in literal.value, (function.name, call.lineno)
                checked += 1
    assert checked > 70


def test_queue_recovery_paused_feedback_selection_is_exact(capsys):
    import json

    namespace, events = _queue_selector_namespace()
    namespace["queue_recovery_main"](["--case-set", "paused-feedback"])
    assert events == ["test_postgres_paused_feedback_history_preserves_ordinary_study"]
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert receipt["scope"] == "paused-feedback-only" and receipt["executed_count"] == 1


def test_paused_feedback_waits_for_the_exact_post_review_generation_and_publication():
    import ast
    from pathlib import Path

    import pytest

    source = (
        Path(__file__).resolve().parents[2]
        / "database/tests/check_postgres_background_workloads.py"
    )
    tree = ast.parse(source.read_text())
    node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_await_queue_generation"
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    wait = namespace["_await_queue_generation"]
    values = iter(
        [
            (
                {"id": "task", "generation": 8, "state": "queued"},
                {"published_refresh_epoch": 6},
            ),
            (
                {"id": "task", "generation": 8, "state": "complete"},
                {"published_refresh_epoch": 6},
            ),
            (
                {"id": "task", "generation": 8, "state": "complete"},
                {"published_refresh_epoch": 7},
            ),
        ]
    )
    wakes, pauses = [], []
    row = wait(
        lambda: next(values),
        lambda: wakes.append(1),
        lambda: 0,
        pauses.append,
        "task",
        8,
        7,
    )
    assert row == {"id": "task", "generation": 8, "state": "complete"}
    assert len(wakes) == 2 and pauses == [0.25, 0.25]
    with pytest.raises(AssertionError, match="generation changed"):
        wait(
            lambda: (
                {"id": "task", "generation": 9, "state": "complete"},
                {"published_refresh_epoch": 7},
            ),
            lambda: None,
            lambda: 0,
            lambda _: None,
            "task",
            8,
            7,
        )
    ticks = iter([0, 91])
    with pytest.raises(AssertionError, match="did not complete"):
        wait(
            lambda: (
                {"id": "task", "generation": 8, "state": "queued"},
                {"published_refresh_epoch": 6},
            ),
            lambda: None,
            lambda: next(ticks),
            lambda _: None,
            "task",
            8,
            7,
        )


def test_queue_recovery_retained_review_selection_is_exact(capsys):
    import json

    namespace, events = _queue_selector_namespace()
    namespace["queue_recovery_main"](["--case-set", "review-recovery"])
    assert events == [
        "test_postgres_retained_review_reconciliation_preserves_original_attempt"
    ]
    receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert receipt["scope"] == "review-recovery-only" and receipt["executed_count"] == 1


def test_p05_position_bridge_selects_one_exact_issued_review_case():
    import importlib.util
    from pathlib import Path

    import pytest

    path = (
        Path(__file__).resolve().parents[2] / "database/tests/position-native-pytest.py"
    )
    spec = importlib.util.spec_from_file_location("p05_position_bridge", path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    assert bridge.selected_nodeid(
        {"TEMPO_NATIVE_POSITION_CHECK": "position-review-atomicity"}
    ) == (
        "database/tests/position_study/test_position_review_transport_atomicity.py::"
        "test_p05_real_issued_review_transport_effect_and_checkpoint_rollback"
    )
    with pytest.raises(RuntimeError, match="Unknown native position check"):
        bridge.selected_nodeid({"TEMPO_NATIVE_POSITION_CHECK": "other"})
