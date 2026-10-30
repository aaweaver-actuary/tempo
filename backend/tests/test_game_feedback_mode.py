"""Managed mode facts and the actual PostgreSQL disable/acceptance fence.

Reuses the existing task-owned PostgreSQL fixture. No live target/configuration,
worker launch, provider request or Study history mutation is performed.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
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
    ManagedGamesModeStore, feedback_handoff_admitted, fence_feedback_handoff, read_feedback_mode,
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


def test_same_container_restart_cannot_reuse_stale_intent_after_failed_clear(tmp_path):
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
