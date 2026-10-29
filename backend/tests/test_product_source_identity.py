"""Provider/canonical identity seam with synthetic facts and injected storage.

These cheap cases prove source binding and command shape, not PostgreSQL locking
or the receiving owner's canonical admission transaction.
"""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.contracts.product_v02.games import GamePage
from app.games.feedback.comparison import normalize_record
from app.games.feedback.coordinator import Coordinator, delivery_for
from app.games.feedback.jobs import FeedbackRunner
from app.games.feedback.ports import ConcurrentUpdate, PageCommit
from app.games.providers.lichess_acquisition import LichessSourceRecord
from app.games.providers.chesscom_acquisition import ChesscomSourceRecord
from app.product_composition.game_history import project_retained_encounters
from app.product_composition.game_sync import WorkflowComparer, WorkflowNormalizer
from app.product_composition.retained_game import retained_canonical_game
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_legacy_database.adapters.games import product_sources as owner
from test_product_feedback_jobs import EncounterReceiver, FakeClock, MemoryStore, spec
from test_product_game_findings import record, snapshot


def inputs(*, revision=7, precision="second", played_at="2026-10-20T12:00:00Z", provider="lichess"):
    original = record(played_at=played_at, precision=precision)
    source_class = LichessSourceRecord if provider == "lichess" else ChesscomSourceRecord
    source = source_class(**original.model_dump(exclude={"source_revision"}),
        source_revision=revision, source_metadata=(("rated", "true"),
            ("speed" if provider == "lichess" else "time_class", '"rapid"')))
    request = spec(provider=provider).model_copy(update={"workspace_id": "workspace", "repertoire_ids": ("rep",)})
    captured = snapshot(mapping_changes=dict(introduction="unintroduced", introduced_at=None))
    return source, request, captured


def raw_library(source, request, **changes):
    prepared = owner._prepare(source, request)
    row = dict(zip(owner.storage.GAME_COLUMNS, owner._values(prepared)))
    row.update(id="existing-canonical-row", provider=prepared.provider, adaptive_excluded=0)
    row.update(changes)
    return row


def bound_store(monkeypatch, row=None):
    state = SimpleNamespace(row=deepcopy(row), writes=[], commits=[])
    @contextmanager
    def connection():
        yield "borrowed-transaction"
    store = owner.PostgresCanonicalGamesStore(connection)
    monkeypatch.setattr(store, "_transaction", connection)
    monkeypatch.setattr(owner, "_current", lambda database, prepared, **kwargs: state.row)
    monkeypatch.setattr(owner.storage, "lock_import_identity", lambda *args: None)
    def insert(database, game_id, provider, values):
        state.row = dict(zip(owner.storage.GAME_COLUMNS, values))
        state.row.update(id=game_id, provider=provider, adaptive_excluded=0)
        state.writes.append("insert")
    def update(database, game_id, values):
        state.row.update(zip(owner.storage.GAME_COLUMNS, values))
        state.writes.append("update")
    monkeypatch.setattr(owner.storage, "insert_imported_game", insert)
    monkeypatch.setattr(owner.storage, "update_imported_game", update)
    monkeypatch.setattr(owner.PostgresGamesStore, "commit_page", lambda self, commit: state.commits.append(commit))
    return store, state


def workflow(source, request, captured, identity):
    class RecordingStore(MemoryStore):
        def commit_page(self, commit):
            super().commit_page(commit)
            self.last_commit = commit
    memory, clock, receiver = RecordingStore(), FakeClock(), EncounterReceiver()
    page = GamePage(provider=request.provider, account=request.account, window=request.window,
        records=(source,), rejections=(), continuation=None, complete=True, saturated=False)
    coordinator = Coordinator(providers={request.provider: SimpleNamespace(fetch_page=lambda **kwargs: page)},
        normalizer=WorkflowNormalizer(), source_identity=identity, comparer=WorkflowComparer(),
        snapshots=SimpleNamespace(snapshot=lambda repertoire: captured), store=memory,
        learning=receiver, clock=clock, encounter_feedback=True)
    return SimpleNamespace(coordinator=coordinator, runner=FeedbackRunner(coordinator),
        memory=memory, receiver=receiver, clock=clock)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("precision", ["second", "day"])
@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_provider_and_retained_replay_share_original_source_and_delivery_key(monkeypatch, existing, precision, provider):
    played_at = "2026-10-20T12:00:00Z" if precision == "second" else "2026-10-20T00:00:00Z"
    source, request, captured = inputs(precision=precision, played_at=played_at, provider=provider)
    # Old storage spelling is authority, not a reason to rewrite its fingerprint.
    row = raw_library(source, request, played_at=played_at.replace("Z", ".000000+00:00"),
        moves_json='[ "d2d4" ]') if existing else None
    if existing and provider == "chesscom":
        row["provider"] = "chesscom"  # Retain an existing supported namespace alias.
    store, state = bound_store(monkeypatch, row)
    h = workflow(source, request, captured, store)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    commit = next(iter(h.memory.captures.values()))
    game = commit.normalized
    effect, = next(iter(h.memory.sources.values())).effects
    assert effect.source.source_revision == game.source_revision == 1 != source.source_revision
    assert game.source_digest != source.source_digest
    # Run the real producer publication guard/writer; borrowed SQL is separately native-tested.
    store.commit_page(PageCommit(job=h.memory.load_job(request.job_id),
        sources=tuple(h.memory.sources.values()), captures=(commit,), deliveries=tuple(h.memory.deliveries.values())))
    canonical = owner._source_header(owner._prepare(source, request), state.row)
    retained = retained_canonical_game({"game": canonical, "authority": {"workspace_id": "workspace"}})
    assert game == retained and game.source_digest == fingerprint(canonical)
    findings = WorkflowComparer().compare(retained, captured, record=source, spec=request)
    replay, = project_retained_encounters(game=retained, provider=provider, snapshot=captured,
        findings=findings, detected_at=h.clock.utc_now(), time_precision=precision)
    assert replay == effect
    assert effect.occurred_at == (game.played_at if precision == "second" else None)
    assert delivery_for("retained-replay", replay).operation_id == next(iter(h.memory.deliveries))
    assert h.runner.run_once() == "delivery"
    duplicate = h.receiver.accept_encounter(replay)
    assert duplicate.outcome == "duplicate" and h.receiver.first_introductions == 1
    assert len(h.receiver.receipts) == 1
    assert commit.record.source_digest == source.source_digest and commit.record.pgn == source.pgn
    assert commit.provider_record_json == source.model_dump_json()
    if existing:
        assert canonical == {column: row[column] for column in owner.SOURCE_COLUMNS.split(",")}


def test_recomparison_preserves_frozen_command_when_acquisition_revision_changes(monkeypatch):
    source, request, captured = inputs()
    store, state = bound_store(monkeypatch, raw_library(source, request))
    h = workflow(source, request, captured, store)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    frozen = next(iter(h.memory.deliveries.values())).command
    later = request.model_copy(update={"job_id": "overlap"})
    newer = source.model_copy(update={"source_revision": 8})
    h.coordinator.providers["lichess"] = SimpleNamespace(fetch_page=lambda **kwargs:
        GamePage(
            provider="lichess", account=request.account, window=request.window, records=(newer,),
            rejections=(), continuation=None, complete=True, saturated=False))
    h.clock.value += 3600
    h.memory.enqueue(later)
    # Explicit page invocation avoids delivery-first runner ordering.
    h.coordinator.capture_page(h.memory.load_job("overlap"))
    assert next(iter(h.memory.deliveries.values())).command == frozen
    assert len(h.memory.deliveries) == 1
    source_state, = h.memory.sources.values()
    assert source_state.original.source_revision == 7 and not source_state.quarantined
    latest, = h.memory.last_commit.captures
    assert latest.record.source_revision == 8
    store.commit_page(PageCommit(job=h.memory.load_job("overlap"), sources=(source_state,), captures=(latest,), deliveries=()))
    assert len(state.commits) == 1 and not state.writes


@pytest.mark.parametrize("change", [
    {"moves_json": '["e2e4"]'}, {"result": "0-1"}, {"username": "AnotherPlayer"},
    {"played_at": "2026-10-21T12:00:00+00:00"},
])
def test_existing_canonical_source_conflict_is_captured_without_overwrite_or_delivery(monkeypatch, change):
    source, request, captured = inputs()
    row = raw_library(source, request, **change)
    store, state = bound_store(monkeypatch, row)
    h = workflow(source, request, captured, store)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    assert h.memory.load_job(request.job_id).status == "quarantined"
    assert not h.memory.deliveries and not state.writes and state.row == row
    assert next(iter(h.memory.captures.values())).record == source


def test_canonical_source_change_between_binding_and_commit_rejects_page(monkeypatch):
    source, request, captured = inputs()
    store, state = bound_store(monkeypatch, raw_library(source, request))
    h = workflow(source, request, captured, store)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    state.row["adaptive_excluded"] = 1
    with pytest.raises(ConcurrentUpdate, match="canonical_game_source_changed"):
        store.commit_page(PageCommit(job=h.memory.load_job(request.job_id),
            sources=tuple(h.memory.sources.values()), captures=tuple(h.memory.captures.values()), deliveries=()))
    assert not state.commits and not state.writes


def test_recomparison_of_changed_canonical_source_holds_original_command(monkeypatch):
    source, request, captured = inputs()
    store, state = bound_store(monkeypatch, raw_library(source, request))
    h = workflow(source, request, captured, store)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    original = next(iter(h.memory.deliveries.values())).command
    state.row["adaptive_excluded"] = 1
    later = request.model_copy(update={"job_id": "canonical-source-changed"})
    h.memory.enqueue(later)
    h.coordinator.capture_page(h.memory.load_job(later.job_id))
    controlled = h.memory.pending_delivery(h.clock.now())
    assert controlled.control.key == original.key and controlled.control.source_revision == 1
    assert controlled.control.action == "hold" and controlled.control.reason == "source_corrected"
    assert next(iter(h.memory.sources.values())).effects == (original,)
    h.coordinator.deliver(controlled)
    assert h.receiver.accept_encounter(original).status == "held"
    assert h.receiver.first_introductions == 0


def test_binding_cannot_skip_pgn_integrity_or_invent_an_occurrence(monkeypatch):
    source, request, captured = inputs()
    store, state = bound_store(monkeypatch)
    corrupted = source.model_copy(update={"source_digest": "0" * 64})
    h = workflow(corrupted, request, captured, store)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    assert not h.memory.deliveries and not state.commits
    unknown = source.model_copy(update={"played_at": None, "time_precision": "unknown"})
    verified = normalize_record(unknown, provider="lichess", account="Learner", workspace_id="workspace")
    with pytest.raises(ValueError, match="occurrence_time_unavailable"):
        store.bind_source(verified, record=unknown, spec=request)


def test_identity_port_cannot_change_verified_history_or_occurrence():
    source, request, captured = inputs()
    for changed in ({"moves": ["e2e4"]}, {"played_at": "2026-10-21T12:00:00Z"}):
        h = workflow(source, request, captured, SimpleNamespace(bind_source=lambda game, **kwargs:
            game.model_copy(update=changed)))
        h.memory.enqueue(request)
        assert h.runner.run_once() == "page"
        assert h.memory.load_job(request.job_id).status == "quarantined" and not h.memory.deliveries


def test_current_factory_injects_source_owner_without_starting_or_receiving(monkeypatch):
    from app.product_composition import game_sync_current as composition
    supplied = {}
    configuration = object()
    def assemble(**kwargs):
        supplied.update(kwargs)
        return "unstarted-runtime"
    monkeypatch.setattr(composition, "PostgresReviewStore", lambda connections:
        SimpleNamespace(configuration=lambda workspace, provider: configuration if provider == "lichess" else None))
    monkeypatch.setattr(composition, "configuration_policy", lambda value: "explicit-policy")
    monkeypatch.setattr(composition, "create_game_sync", assemble)
    monkeypatch.setattr(composition, "create_game_review", lambda **kwargs: "unstarted-review")
    def forbidden():
        pytest.fail("factory opened a source connection")
    runtime, schedule = composition.create_current_game_sync(workspace="workspace", connections=forbidden,
        learning="explicit-qualified-receiver", clock=FakeClock())
    assert runtime == "unstarted-runtime" and schedule.schedule == "unstarted-review"
    assert supplied["source_identity"] is supplied["store"]
    assert isinstance(supplied["source_identity"], owner.PostgresCanonicalGamesStore)
    assert supplied["encounter_feedback"] is True and supplied["learning"] == "explicit-qualified-receiver"
