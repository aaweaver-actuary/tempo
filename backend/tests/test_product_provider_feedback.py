"""Provider composition order and admission, without network or native fixtures.

Real receipt persistence/replay remains the separate, task-owned PostgreSQL seam.
"""
from contextlib import contextmanager, nullcontext
from threading import Event
from types import SimpleNamespace

import pytest

from app.contracts.product_v02.learning import EncounterReceipt, GameLapse
from app.games.feedback.review import ReviewUnavailable
from app.product_composition import game_sync_current as current
from app.product_composition import provider_feedback as binding
from app.product_composition.game_sync import GameSyncRuntime
from app.services.activity_gate import activity_gate
from app.services.redis_admission_gate import BackgroundAdmissionDeferred
from test_product_encounter_projection import inputs, project
from test_product_feedback_jobs import EncounterReceiver, FakeClock
from test_product_game_lapse import fixture


@pytest.fixture
def command():
    game, snapshot, findings, source = inputs(count=2)
    value, = project(game, snapshot, findings, source)
    return value


def receiver(command, *, stopped=lambda: False, fail_commit=False):
    events, receipt = [], EncounterReceiver().accept_encounter(command)
    retained, snapshot, database = object(), object(), object()

    def capture(original):
        assert original is command
        events.append("capture")
        return retained

    def project_capture(actual):
        assert actual is retained
        events.append("project")
        return snapshot

    @contextmanager
    def write():
        events.append("writer")
        try:
            yield database
            if fail_commit:
                raise RuntimeError("commit_failed")
            events.append("committed")
        except Exception:
            events.append("rolled_back")
            raise

    def fence(db, original, actual):
        assert (db, original, actual) == (database, command, retained)
        events.append("fence")

    def receive(db, **values):
        assert db is database and values["command"] is command
        assert values["capture"] is retained and values["snapshot"] is snapshot
        assert values["introduction_policy"].allowance == "encounter_exception"
        assert values["operation_stopped"] is stopped
        events.append("owner")
        return receipt

    adapter = binding.CurrentProviderFeedback(read=lambda: pytest.fail("read on delivery"),
        write=write, operation_stopped=stopped, clock=FakeClock(), capture=capture,
        project=project_capture, fence=fence, receive=receive)
    return adapter, events, receipt


def test_original_provider_command_is_acknowledged_only_after_owner_commit(command):
    adapter, events, receipt = receiver(command)
    assert adapter.accept_encounter(command) is receipt
    assert events == ["capture", "project", "writer", "fence", "owner", "committed"]


@pytest.mark.parametrize("stopped", [True, None])
def test_stopped_or_unknown_provider_control_cannot_capture_or_write(command, stopped):
    adapter, events, _ = receiver(command, stopped=lambda: stopped)
    with pytest.raises(BackgroundAdmissionDeferred):
        adapter.accept_encounter(command)
    assert events == []


def test_control_change_during_capture_does_not_open_a_writer(command):
    values = iter((False, True))
    adapter, events, _ = receiver(command, stopped=lambda: next(values))
    with pytest.raises(BackgroundAdmissionDeferred):
        adapter.accept_encounter(command)
    assert events == ["capture", "project"]


def test_failed_commit_does_not_return_the_provisional_receipt(command):
    adapter, events, _ = receiver(command, fail_commit=True)
    with pytest.raises(RuntimeError, match="commit_failed"):
        adapter.accept_encounter(command)
    assert events[-2:] == ["owner", "rolled_back"]


def test_source_fence_failure_never_calls_study(command):
    adapter, events, _ = receiver(command)
    def changed(*_):
        raise ReviewUnavailable("canonical_imported_game_changed_or_excluded")
    adapter.fence = changed
    with pytest.raises(ReviewUnavailable, match="changed_or_excluded"):
        adapter.accept_encounter(command)
    assert events == ["capture", "project", "writer", "rolled_back"]


@pytest.mark.parametrize("method", ["encounter_status", "effect_status"])
def test_status_uses_reader_and_preserves_real_encounter_receipt_shape(command, method):
    adapter, _, receipt = receiver(command)
    reads = []
    @contextmanager
    def read():
        reads.append("reader")
        yield "query-only"
    adapter.read = read
    adapter.write = lambda: pytest.fail("status requested a writer")
    def owner(db):
        assert db == "query-only"
        return SimpleNamespace(encounter_status=lambda key: receipt,
            effect_status=lambda key: pytest.fail("encounter was downcast"))
    adapter._status_owner = owner
    assert getattr(adapter, method)(command.key) is receipt
    assert isinstance(receipt, EncounterReceipt) and reads == ["reader"]


def test_existing_lapse_uses_the_same_frozen_source_fence_and_canonical_owner(monkeypatch):
    command = fixture("lapse", GameLapse)
    events, capture, snapshot, receipt = [], object(), object(), object()
    @contextmanager
    def write():
        events.append("writer")
        yield "borrowed"
        events.append("committed")
    def owner(db, **facts):
        assert db == "borrowed" and facts["retained_owner_capture"] is capture
        assert facts["snapshot"] is snapshot
        def accept(original):
            assert original is command
            events.append("lapse_owner")
            return receipt
        return SimpleNamespace(accept_lapse=accept)
    monkeypatch.setattr(binding, "current_owner_game_feedback", owner)
    monkeypatch.setattr(activity_gate, "check_background_admission", lambda: events.append("foreground_check"))
    adapter = binding.CurrentProviderFeedback(read=lambda: pytest.fail("unexpected read"), write=write,
        operation_stopped=lambda: False, clock=FakeClock(), capture=lambda original: capture,
        project=lambda retained: snapshot, fence=lambda *args: events.append("source_fence"))
    assert adapter.accept_lapse(command) is receipt
    assert events == ["writer", "source_fence", "foreground_check", "lapse_owner", "committed"]


def test_concrete_factory_binds_actual_admitted_context_stop_and_explicit_profiles(monkeypatch):
    captured, stop = {}, Event()
    runtime = GameSyncRuntime(object(), stop)
    def build(**values):
        captured.update(values)
        return runtime, "schedule"
    monkeypatch.setattr(current, "create_current_game_sync", build)
    monkeypatch.setenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED", "1")
    from app.services import study_priority
    policy = dict(available=True, active=False, study_only_mode=True)
    monkeypatch.setattr(study_priority, "control_snapshot", lambda: policy)
    monkeypatch.setattr(activity_gate, "background_job", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(activity_gate, "check_background_admission", lambda: None)
    writer = SimpleNamespace(read_only=False, writer=lambda: None)
    reader = SimpleNamespace(read_only=True, reader=lambda: None)
    assert current.create_current_provider_worker(workspace="legacy", connections=writer,
        read_connections=reader) == (runtime, "schedule")
    learning = captured["learning"]
    assert learning.operation_stopped() is None
    with current.current_sync_admission():
        assert learning.operation_stopped() is False
        policy["active"] = True
        with pytest.raises(BackgroundAdmissionDeferred):
            learning.operation_stopped()
        policy["active"] = False
        stop.set()
        assert learning.operation_stopped() is True
    assert learning.operation_stopped() is None
    assert captured["connections"] is writer


@pytest.mark.parametrize("writer_read_only,reader_read_only", [(True, True), (False, False)])
def test_concrete_factory_rejects_mixed_profiles_before_any_database_access(writer_read_only, reader_read_only):
    with pytest.raises(ReviewUnavailable, match="explicit_games_reader_and_writer_profiles_required"):
        current.create_current_provider_worker(workspace="legacy",
            connections=SimpleNamespace(read_only=writer_read_only),
            read_connections=SimpleNamespace(read_only=reader_read_only))


def test_writer_profile_cannot_be_used_for_receipt_reads():
    from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections
    with pytest.raises(ReviewUnavailable, match="reader_profile_required"):
        with DedicatedGamesConnections("unused-writer").reader():
            pytest.fail("writer profile entered query path")


@pytest.mark.parametrize("fault", [None, "excluded", "changed", "quarantined", "uncommitted", "relabeled"])
def test_games_authenticates_frozen_effect_and_current_canonical_source(monkeypatch, fault):
    from copy import deepcopy
    from tempo_legacy_database.adapters.games import product_review, product_sources
    from tempo_legacy_database.adapters.games.authority import acceptance
    from test_product_source_identity import inputs, raw_library, bound_store, workflow

    original, request, snapshot = inputs()
    raw = raw_library(original, request)
    identity, _ = bound_store(monkeypatch, raw)
    h = workflow(original, request, snapshot, identity)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    source, = h.memory.sources.values()
    command, = source.effects
    header = {name: raw[name] for name in product_sources.SOURCE_COLUMNS.split(",")}
    actual = deepcopy(header)
    if fault == "excluded":
        actual["adaptive_excluded"] = 1
    elif fault == "changed":
        actual["moves_json"] = '["e2e4"]'
    elif fault == "quarantined":
        source = source.model_copy(update={"quarantined": True})
    elif fault == "uncommitted":
        source = source.model_copy(update={"effects": ()})
    elif fault == "relabeled":
        command = command.model_copy(update={"detected_at": "2026-10-27T12:00:00Z"})
    events = []
    monkeypatch.setattr(acceptance, "lock_imported_source", lambda *args: events.append("source_lock"))
    monkeypatch.setattr(acceptance, "imported_game", lambda *args: actual)
    database = SimpleNamespace(execute_native=lambda *args: SimpleNamespace(
        fetchone=lambda: (source.model_dump_json(),)))
    retained = dict(game=header, authority=dict(workspace_id=request.workspace_id))
    if fault is None:
        product_review.fence_imported_effect(database, command, retained)
    else:
        with pytest.raises(ReviewUnavailable):
            product_review.fence_imported_effect(database, command, retained)
    assert events == ["source_lock"]
