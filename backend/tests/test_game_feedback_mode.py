"""Managed mode facts and the actual PostgreSQL disable/acceptance fence.

Reuses the existing task-owned PostgreSQL fixture. No live target/configuration,
worker launch, provider request or Study history mutation is performed.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
for source in ("packages/game-review-contracts/src", "packages/chess-core/src", "packages/evidence-math/src",
               "packages/scheduling/src", "packages/game-classification/src", "packages/game-ingestion/src",
               "packages/repertoire-comparison/src", "contracts/game-evidence/src", "contracts/analysis-jobs/src",
               "services/learning/application/src", "services/learning/runtime/src", "services/learning/remediation/src"):
    sys.path.insert(0, str(ROOT / source))

from app.games.feedback.interfaces.managed_mode import ManagedGamesModeIntent, ManagedModeAdmissionDenied
from app.product_composition.game_feedback_mode import main
from app.product_composition.managed_runtime import current_managed_runtime_identity, initialize_runtime_identity
from tempo_legacy_database.adapters.games.managed_mode import (
    ManagedGamesModeStore, feedback_handoff_admitted, feedback_handoff_admitted_in_transaction,
    fence_feedback_handoff, read_feedback_mode,
)
from test_product_game_lapse_postgres import owned_postgres

RUNTIME = "b" * 12 + ":" + "2" * 32


def intent(workspace="fixture", *, generation=1, mode="feedback", phase="active"):
    active = phase == "active"
    runtimes = [dict(service=service, container_id=letter * 64, hostname=letter * 12, boot_nonce="2" * 32,
                     image="sha256:" + "d" * 64)
                for service, letter in (("api", "a"), ("foreground-worker", "b"), ("game-feedback-worker", "c"))]
    return ManagedGamesModeIntent.model_validate_json(json.dumps(dict(schema_version=1, workspace_id=workspace, generation=generation,
        mode=mode, phase=phase, operation_id="synthetic-operation", release_revision="e" * 40,
        collector_image="sha256:" + "d" * 64, configuration_fingerprint="f" * 64, target_fingerprint="1" * 64,
        runtimes=runtimes if active else [], updated_at="2026-10-30T04:00:00Z",
        verified_at="2026-10-30T04:00:00Z" if active else None)))


@pytest.mark.parametrize("mode,phase,admitted", [("feedback", "active", True), ("feedback", "inactive", False),
    ("feedback", "transitioning", False), ("collect-only", "active", False)])
def test_only_verified_feedback_admits_actual_bound_runtime(mode, phase, admitted):
    value = intent(mode=mode, phase=phase)
    assert value.admits(RUNTIME) is admitted
    assert not value.admits("9" * 12 + ":" + "2" * 32)  # Foreign command worker is not the verified one.


def test_same_container_restart_cannot_reuse_stale_intent_after_failed_clear(tmp_path, monkeypatch):
    from app.product_composition import managed_runtime
    monkeypatch.setattr(managed_runtime, "_current_boot_process", lambda: "a" * 32 + ":100")
    path = tmp_path / "runtime.json"
    first = initialize_runtime_identity(path, hostname="b" * 12)
    payload = intent().model_dump(mode="json")
    payload["runtimes"][1]["boot_nonce"] = first["boot_nonce"]
    old_intent = ManagedGamesModeIntent.model_validate_json(json.dumps(payload))
    assert old_intent.admits(current_managed_runtime_identity(path, hostname="b" * 12))
    # Failed inactive clear leaves the old DB intent. Restart the SAME hostname.
    second = initialize_runtime_identity(path, hostname="b" * 12)
    assert second["boot_nonce"] != first["boot_nonce"]
    assert not old_intent.admits(current_managed_runtime_identity(path, hostname="b" * 12))
    path.unlink()
    assert current_managed_runtime_identity(path, hostname="b" * 12) is None


def test_failed_optional_identity_initialization_keeps_backend_startup_and_denies_stale_file(tmp_path, monkeypatch):
    from app.product_composition import managed_runtime
    path = tmp_path / "runtime.json"
    monkeypatch.setattr(managed_runtime, "_current_boot_process", lambda: "a" * 32 + ":100")
    initialize_runtime_identity(path, hostname="b" * 12)
    original = path.read_text()
    monkeypatch.setattr(managed_runtime, "_current_boot_process", lambda: "a" * 32 + ":200")
    def unavailable(*args, **kwargs):
        raise PermissionError("synthetic_read_only_identity_path")
    monkeypatch.setattr(managed_runtime.os, "replace", unavailable)
    with pytest.raises(PermissionError):
        initialize_runtime_identity(path, hostname="b" * 12)
    assert path.read_text() == original
    assert current_managed_runtime_identity(path, hostname="b" * 12) is None
    monkeypatch.setattr(managed_runtime, "_current_boot_process", lambda: None)
    assert current_managed_runtime_identity(path, hostname="b" * 12) is None
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\nexit 2\n")
    executable.chmod(0o700)
    started = subprocess.run(["/bin/sh", str(ROOT / "backend/managed-runtime-entrypoint.sh"),
        "/bin/sh", "-c", "printf backend-started"], env=dict(os.environ, PATH=str(tmp_path)),
        capture_output=True, text=True, check=False)
    assert started.returncode == 0 and started.stdout == "backend-started"


def test_boot_process_read_is_bounded_and_rejects_unreadable_or_malformed_proc(monkeypatch):
    from io import StringIO
    from app.product_composition import managed_runtime
    def read(path):
        return StringIO("a" * 32 if str(path).endswith("boot_id") else
            "1 (backend (worker)) " + " ".join(["S"] + ["0"] * 18 + ["12345"]))
    monkeypatch.setattr(managed_runtime.Path, "open", read)
    assert managed_runtime._current_boot_process() == "a" * 32 + ":12345"
    monkeypatch.setattr(managed_runtime.Path, "open", lambda path: StringIO("unavailable"))
    assert managed_runtime._current_boot_process() is None


@pytest.mark.parametrize("fault", ["missing_command_worker", "wrong_container", "wrong_worker_image", "inactive_verified", "duplicate_service"])
def test_managed_mode_rejects_unverified_or_contradictory_runtime_claims(fault):
    payload = intent().model_dump(mode="json")
    if fault == "missing_command_worker": payload["runtimes"].pop(1)
    elif fault == "wrong_container": payload["runtimes"][0]["hostname"] = "9" * 12
    elif fault == "wrong_worker_image": payload["runtimes"][2]["image"] = "sha256:" + "9" * 64
    elif fault == "inactive_verified": payload["phase"] = "inactive"
    else: payload["runtimes"][1]["service"] = "api"
    with pytest.raises(ValidationError):
        ManagedGamesModeIntent.model_validate_json(json.dumps(payload))


def test_mode_record_cli_requires_explicit_apply_before_constructing_writer():
    def forbidden(): pytest.fail("writer opened without explicit apply")
    with pytest.raises(SystemExit) as failed:
        main(["record", "--intent-json", intent().model_dump_json(), "--expected-generation", "0"], writer_factory=forbidden)
    assert failed.value.code == 2


@pytest.mark.parametrize("flags,enabled", [([], False), (["--collect-only"], False), (["--feedback"], True)])
def test_current_worker_cli_requires_explicit_feedback_instead_of_removed_collect_flag(monkeypatch, flags, enabled):
    from app.product_composition import game_sync_current as current
    from app.product_composition import game_collection_status as status
    seen = []
    monkeypatch.setenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED", "1")
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", "synthetic-connection-never-opened")
    monkeypatch.setattr(sys, "argv", ["game_sync_current", *flags])
    monkeypatch.setattr(current, "DedicatedGamesConnections", lambda dsn: object())
    def worker(**options):
        seen.append(options)
        return object(), object()
    monkeypatch.setattr(current, "current_provider_worker", worker)
    monkeypatch.setattr(current, "run_optional_worker", lambda *args, **kwargs: 0)
    monkeypatch.setattr(status, "feedback_release_capabilities", lambda: {"availability": "available",
        "selector": True, "current_authority": True, "again": True, "canonical_introduction": True,
        "retained_qualification": True, "foreground_handoff": True, "issuance_fence": True})
    current.main()
    assert seen[0]["delivery_enabled"] is enabled


def test_current_worker_cli_rejects_conflicting_mode_before_opening_connections(monkeypatch):
    from app.product_composition import game_sync_current as current
    monkeypatch.setattr(sys, "argv", ["game_sync_current", "--collect-only", "--feedback"])
    with pytest.raises(SystemExit) as failed:
        current.main()
    assert failed.value.code == 2


def test_feedback_cli_refuses_an_unqualified_composition_before_constructing_receiver(monkeypatch):
    from app.product_composition import game_sync_current as current
    from app.product_composition import game_collection_status as status
    monkeypatch.setenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED", "1")
    monkeypatch.setattr(sys, "argv", ["game_sync_current", "--feedback"])
    monkeypatch.setattr(status, "feedback_release_capabilities", lambda: {"availability": "unavailable"})
    monkeypatch.setattr(current, "current_provider_worker", lambda **kwargs: pytest.fail("unqualified receiver constructed"))
    with pytest.raises(Exception, match="qualified_feedback_composition_required"):
        current.main()



def test_feedback_capability_read_is_explicit_typed_and_never_inferred_from_http_or_receipt():
    from app.product_composition.game_collection_status import feedback_release_capabilities, _encode
    complete = dict(policy_version="first_departure_v1", receipt_interface_version="0.2.2",
        selector=True, current_authority=True, again=True, canonical_introduction=True,
        retained_qualification=True, foreground_handoff=True, issuance_fence=True)
    assert feedback_release_capabilities(lambda: complete)["availability"] == "available"
    assert feedback_release_capabilities(lambda: complete | {"again": False})["again"] is False
    assert feedback_release_capabilities(lambda: {"status": "available"})["availability"] == "unavailable"
    encoded = _encode({"identity": {}, "managed_mode": {"runtimes": [dict(
        service="foreground-worker", container_id="b" * 64, hostname="b" * 12, boot_nonce="2" * 32)]}})
    assert "b" * 64 not in encoded and "2" * 32 not in encoded


@pytest.fixture
def mode_case(owned_postgres):
    workspace = "mode-" + uuid.uuid4().hex

    @contextmanager
    def connections():
        with owned_postgres() as database:
            database.execute_native("SET LOCAL transaction_timeout='250ms'")
            database.execute_native("SET LOCAL lock_timeout='100ms'")
            database.execute_native("SET LOCAL statement_timeout='250ms'")
            yield database.raw

    store = ManagedGamesModeStore(connections)
    value = intent(workspace)
    store.record(value, expected_generation=0)
    return workspace, connections, store, value, owned_postgres


def test_native_shared_mode_fence_prevents_disable_commit_until_handoff_finishes(mode_case):
    workspace, connections, store, value, transaction = mode_case
    disabled = intent(workspace, generation=2, phase="inactive")
    with transaction() as database:
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        database.execute_native("SET LOCAL lock_timeout='100ms'")
        assert fence_feedback_handoff(database, workspace, runtime_id=RUNTIME) == value
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempted_disable = pool.submit(store.record, disabled, expected_generation=1)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                attempted_disable.result(timeout=1)
        # The short borrowed writer still owns the shared fence; no helper commit occurred.
        assert database.raw.info.transaction_status.name == "INTRANS"
    store.record(disabled, expected_generation=1)
    assert not feedback_handoff_admitted(workspace, connections, runtime_id=RUNTIME)
    with transaction() as database:
        with pytest.raises(ManagedModeAdmissionDenied):
            fence_feedback_handoff(database, workspace, runtime_id=RUNTIME)


def test_native_transition_wins_over_stale_outside_admission_snapshot(mode_case):
    workspace, connections, store, value, transaction = mode_case
    assert feedback_handoff_admitted(workspace, connections, runtime_id=RUNTIME)
    transition = intent(workspace, generation=2, phase="transitioning")
    with transaction() as changing:
        changing.execute_native("SET LOCAL transaction_timeout='250ms'")
        changing.execute_native("""UPDATE product_feedback_managed_mode SET generation=2,phase='transitioning',payload=%s::jsonb
            WHERE workspace_id=%s""", (transition.model_dump_json(), workspace))
        def stale_handoff():
            with transaction() as database:
                database.execute_native("SET LOCAL transaction_timeout='250ms'")
                return fence_feedback_handoff(database, workspace, runtime_id=RUNTIME)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                pool.submit(stale_handoff).result(timeout=1)
    assert read_feedback_mode(connections, workspace) == transition
    assert not feedback_handoff_admitted(workspace, connections, runtime_id=RUNTIME)
    with pytest.raises(ValueError, match="generation_changed"):
        store.record(intent(workspace, generation=2), expected_generation=1)


def test_native_borrowed_mode_hint_uses_no_connection_or_row_fence(mode_case):
    workspace, connections, store, value, transaction = mode_case
    with transaction() as database:
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        before = database.execute_native("SELECT pg_backend_pid(),pg_current_xact_id()").fetchone()
        assert feedback_handoff_admitted_in_transaction(database, workspace, runtime_id=RUNTIME)
        # The exclusive update commits while this caller stays open: a hint has
        # no row fence and never borrows another connection.
        with ThreadPoolExecutor(max_workers=1) as pool:
            disabled = intent(workspace, generation=2, phase="inactive")
            assert pool.submit(store.record, disabled, expected_generation=1).result(timeout=1) == disabled
        assert not feedback_handoff_admitted_in_transaction(database, workspace, runtime_id=RUNTIME)
        after = database.execute_native("SELECT pg_backend_pid(),pg_current_xact_id()").fetchone()
        assert (after[0], after[1]) == (before[0], before[1])
        assert database.raw.info.transaction_status.name == "INTRANS"


def test_native_borrowed_mode_hint_isolates_optional_schema_and_payload_failure(mode_case):
    workspace, connections, store, value, transaction = mode_case
    with transaction() as database:
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        database.execute_native("SET LOCAL search_path=pg_catalog")
        assert not feedback_handoff_admitted_in_transaction(database, workspace, runtime_id=RUNTIME)
        assert database.execute_native("SELECT 1").fetchone()[0] == 1
        database.execute_native("SET LOCAL search_path=public")
        assert feedback_handoff_admitted_in_transaction(database, workspace, runtime_id=RUNTIME)
        database.execute_native("UPDATE product_feedback_managed_mode SET payload='{}'::jsonb WHERE workspace_id=%s", (workspace,))
        assert not feedback_handoff_admitted_in_transaction(database, workspace, runtime_id=RUNTIME)
        assert database.execute_native("SELECT 1").fetchone()[0] == 1
        assert database.raw.info.transaction_status.name == "INTRANS"


def test_native_mode_status_is_read_only_and_cannot_lend_write_authority(mode_case):
    workspace, connections, store, value, transaction = mode_case
    seen = []

    @contextmanager
    def observed():
        with connections() as database:
            class Reader:
                def execute(self, statement, parameters=()):
                    seen.append(statement)
                    return database.execute(statement, parameters)
            yield Reader()

    assert read_feedback_mode(observed, workspace) == value
    assert seen[0] == "SET TRANSACTION READ ONLY"
    assert len(seen) == 2 and seen[1].startswith("SELECT ")
    assert not feedback_handoff_admitted("absent-" + workspace, connections, runtime_id=RUNTIME)
    with connections() as database:
        database.execute("SET TRANSACTION READ ONLY")
        assert database.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            database.execute("DELETE FROM product_feedback_managed_mode WHERE workspace_id=%s", (workspace,))


def test_native_same_hostname_new_boot_nonce_denies_stale_verified_intent(mode_case):
    workspace, connections, store, value, transaction = mode_case
    restarted = "b" * 12 + ":" + "3" * 32
    assert feedback_handoff_admitted(workspace, connections, runtime_id=RUNTIME)
    assert not feedback_handoff_admitted(workspace, connections, runtime_id=restarted)
    with transaction() as database:
        with pytest.raises(ManagedModeAdmissionDenied):
            fence_feedback_handoff(database, workspace, runtime_id=restarted)
    with transaction() as database:
        with pytest.raises(ManagedModeAdmissionDenied):
            fence_feedback_handoff(database, workspace)  # No trusted identity never opens a fence.


def test_native_original_effect_control_lookup_uses_its_narrow_index(owned_postgres):
    identity = "mode-index-" + uuid.uuid4().hex
    with owned_postgres() as database:
        database.execute_native("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,0,'{}'::jsonb)", (identity,))
        database.execute_native("""INSERT INTO product_feedback_deliveries
            (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
            SELECT %s||':'||series,%s,'unrelated:'||series,'encounter','acknowledged',0,'{}'::jsonb
            FROM generate_series(1,5000) series""", (identity, identity))
        database.execute_native("""INSERT INTO product_feedback_deliveries
            (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
            VALUES(%s,%s,%s,'control','pending',0,'{}'::jsonb),
                  (%s,%s,%s,'control','superseded',0,'{}'::jsonb)""",
            (identity+":current", identity, identity, identity+":old", identity, identity))
        database.execute_native("ANALYZE product_feedback_deliveries")
        query = """SELECT operation_id FROM product_feedback_deliveries WHERE effect_key=%s
            AND command_kind='control' AND state IN ('pending','acknowledged')"""
        result = database.execute_native(query, (identity,)).fetchall()
        assert [row[0] for row in result] == [identity+":current"]
        plan = database.execute_native("EXPLAIN (FORMAT JSON) " + query, (identity,)).fetchone()[0]
        def indexes(node):
            yield node.get("Index Name")
            for child in node.get("Plans", []):
                yield from indexes(child)
        assert "product_feedback_effect_controls" in set(indexes(plan[0]["Plan"]))
