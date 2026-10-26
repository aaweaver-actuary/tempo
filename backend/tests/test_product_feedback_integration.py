"""Synthetic source integration; native PostgreSQL/product proof is separate.

The existing physical mapping is supplied by the shared snapshot fixture, never
created from the PGN or a matching position. No live data is used.
"""
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.contracts.product_v02.games import GameRecord
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.games.feedback.comparison import normalize_record
from app.product_composition.feedback import main, prepare_game_feedback, prepare_record_feedback, submit_feedback
from app.study.game_feedback import GameFeedback
from test_product_game_lapse import Decisions, MemoryStore, NOW
# Reuse its capped, labelled, isolated fixture only; none of the leaf tests run.
from test_product_game_lapse_postgres import owned_postgres

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures"
PGN = '[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. d4 1-0\n'


@pytest.fixture
def inputs():
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(
        json.loads((FIXTURES / "snapshot.json").read_text())["payload"]))
    record = GameRecord(source_game_id="already-imported-synthetic-game", source_revision=1,
        source_digest=hashlib.sha256(PGN.encode()).hexdigest(), pgn=PGN,
        played_at="2026-10-25T12:00:00Z", time_precision="second")
    return record, snapshot


def prepare(inputs, **changes):
    record, snapshot = inputs
    return prepare_record_feedback(record, provider="chesscom", account="Learner",
        workspace_id=snapshot.membership.workspace_id, snapshot=snapshot,
        detected_at=changes.pop("detected_at", NOW.isoformat()), **changes)


def test_imported_game_maps_to_existing_card(inputs):
    prepared = prepare(inputs)
    mapping = inputs[1].decision_presentations[0]
    assert prepared.lapse.finding.presentation_id == mapping.presentation_id == "existing-physical-card"
    assert prepared.lapse.finding.decision_id == mapping.decision_id
    assert prepared.lapse.finding.played_uci == "d2d4"
    assert prepared.lapse.finding.expected_uci == ("e2e4",)
    assert prepared.lapse.finding.ply == 1
    assert prepared.snapshot_digest == inputs[1].membership.digest


def test_duplicate_pipeline_has_one_lapse(inputs):
    store, decisions = MemoryStore(), Decisions()
    owner = GameFeedback(store, decisions, clock=lambda: NOW)
    first = prepare(inputs)
    receipt = submit_feedback(first, owner)
    retried = prepare(inputs, detected_at=(NOW + timedelta(days=1)).isoformat())
    duplicate = submit_feedback(retried, owner)
    assert first.operation_identity == retried.operation_identity
    assert receipt.status == duplicate.status == "applied"
    assert duplicate.outcome == "duplicate"
    assert receipt.available_by == duplicate.available_by
    assert len(store.reviews) == len(store.effects) == 1
    assert store.history == ["retained-original-review"]


def test_missing_mapping_cannot_prepare_a_lapse(inputs):
    record, snapshot = inputs
    unmapped = snapshot.model_copy(update={"decision_presentations": ()})
    with pytest.raises(ValueError, match="one_confirmed_decision_occurrence_required"):
        prepare((record, unmapped))


def test_retained_canonical_import_needs_no_original_pgn(inputs, monkeypatch):
    record, snapshot = inputs
    game = normalize_record(record, provider="chesscom", account="Learner",
        workspace_id=snapshot.membership.workspace_id)
    from tempo_game_ingestion import PgnGameIngester
    def forbidden(*args, **kwargs):
        raise AssertionError("an already-imported game must not require reconstructed PGN")
    monkeypatch.setattr(PgnGameIngester, "ingest", forbidden)
    prepared = prepare_game_feedback(game, provider="chesscom", time_precision="second",
        snapshot=snapshot, detected_at=NOW.isoformat())
    assert prepared.lapse.finding.source_digest == record.source_digest
    assert prepared.lapse.key.game_id == game.game_id
    with pytest.raises(ValueError, match="one_confirmed_decision_occurrence_required"):
        prepare_game_feedback(game, provider="chesscom", time_precision="unknown",
            snapshot=snapshot, detected_at=NOW.isoformat())


def test_preview_is_private_read_only_and_never_overwrites_retry_payload(inputs, tmp_path, capsys):
    record, snapshot = inputs
    record_path, snapshot_path, output = (tmp_path / name for name in ("record.json", "snapshot.json", "preview.json"))
    record_path.write_text(record.model_dump_json())
    snapshot_path.write_text(snapshot.model_dump_json())
    arguments = ["--record", str(record_path), "--snapshot", str(snapshot_path),
        "--provider", "chesscom", "--account", "Learner", "--workspace-id",
        snapshot.membership.workspace_id, "--detected-at", NOW.isoformat(), "--output", str(output)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == dict(prepared=True, delivered=False, read_only=True)
    saved = output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        main(arguments)
    assert output.read_bytes() == saved


@pytest.fixture
def native_case(inputs, owned_postgres, monkeypatch):
    """Real PostgreSQL and supplied current facts; source fencing is separate."""
    from app import queue_commands
    from app.services import learning_review_adapter
    from tempo_legacy_database.adapters import postgres_store

    class ReviewDate(date):
        @classmethod
        def today(cls):
            return NOW.date()

    monkeypatch.setattr(queue_commands, "date", ReviewDate)
    monkeypatch.setattr(learning_review_adapter, "date", ReviewDate)
    # The fixture really is PostgreSQL. Select that existing owner path without
    # configuring any external DSN or starting a compatibility coordinator.
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    record, snapshot = inputs
    identity = uuid.uuid4().hex
    card_id = "feedback-integration-" + identity
    mapping = snapshot.decision_presentations[0].model_copy(update={"presentation_id": card_id})
    snapshot = snapshot.model_copy(update={"decision_presentations": (mapping,)})
    record = record.model_copy(update={"source_game_id": "imported-" + identity})
    prepared = prepare((record, snapshot))
    decisions = Decisions()
    decisions.mappings = (mapping,)
    state = MemoryStore().card
    with owned_postgres() as database:
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type,trained_color)
            VALUES(%s,'white','prefix',%s,'["e2e4"]','mature',%s,%s,1,20,%s,%s,20,'opening','white')""",
            (card_id, mapping.position_key + " 0 1", state.due_date, mapping.introduced_at,
             state.fsrs_card_json, state.first_correct_at))
        database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('white',%s)", (card_id,))
        history = database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            internal_rating,guided,source_kind,source_ref) VALUES(%s,'correct',%s,10,20,'good',0,'study',%s) RETURNING id""",
            (card_id, (NOW-timedelta(days=3)).isoformat(), "retained:" + identity)).fetchone()[0]
    case = SimpleNamespace(transaction=owned_postgres, card_id=card_id, prepared=prepared,
        decisions=decisions, history_id=history, identity=identity)
    try:
        yield case
    finally:
        # End-of-case cleanup only: retain all rows throughout its retry,
        # readback and controller assertions, then remove this fixture's exact
        # identities so its protected head/source cannot leak into another case.
        with owned_postgres() as database:
            ids = [card_id, card_id + "-active"]
            repertoire = card_id + "-repertoire"
            database.execute_native("DELETE FROM study_game_feedback WHERE card_id=ANY(%s)", (ids,))
            database.execute_native("DELETE FROM cards WHERE id=ANY(%s)", (ids,))
            database.execute_native("DELETE FROM imported_games WHERE id=%s", (record.source_game_id,))
            database.execute_native("DELETE FROM background_tasks WHERE deduplication_key=%s", (repertoire,))
            database.execute_native("DELETE FROM repertoires WHERE id=%s", (repertoire,))


def _native_owner(case, database, now=NOW):
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    return GameFeedback(PostgresGameFeedbackStore(database), case.decisions, clock=lambda: now)


def _issue(database, card_id):
    from app.queue_commands import issue_queue_head
    entry = database.execute_native("""SELECT id FROM daily_queue
        WHERE card_id=%s AND queue_date=%s AND status='queued' ORDER BY position,id LIMIT 1""",
        (card_id, NOW.date().isoformat())).fetchone()[0]
    assert issue_queue_head(database, dict(entry_id=entry, card_id=card_id,
        expected_revision=1, queue_date=NOW.date().isoformat())) == {"issued": True}
    return entry


def _ordinary_review(database, card_id, entry, attempt_id, outcome="correct"):
    """Same controller, adapter, issuance and receipt path as ordinary Study."""
    import tempo_learning_review as learning
    from app.contracts.transport.study import ReviewRequest
    from app.services.learning_review_adapter import LearningReviewAdapter, command_from_request
    request = ReviewRequest(outcome=outcome, guided=False, queue_entry_id=entry,
        expected_revision=1, attempt_id=attempt_id)
    adapter = LearningReviewAdapter(database, request, observed_at=NOW+timedelta(hours=1),
        local_day=NOW.date(), policy_provider=lambda: SimpleNamespace(timezone="UTC", light_first_interval_days=7))
    command = command_from_request(card_id, request)
    result = learning.submit_review(adapter, command, lambda: adapter.context(command)).as_dict()
    adapter.finish_compatibility_effects()
    return result


def test_normal_study_review_settles_obligation(native_case):
    case = native_case
    with case.transaction() as database:
        admitted = submit_feedback(case.prepared, _native_owner(case, database))
        assert admitted.status == "applied"
    # New connection/controller: no producer object, fake queue or direct
    # settlement invocation participates in the genuine review.
    with case.transaction() as database:
        entry = _issue(database, case.card_id)
        first = _ordinary_review(database, case.card_id, entry, "normal:" + case.identity)
        replay = _ordinary_review(database, case.card_id, entry, "normal:" + case.identity)
        assert first["persisted"] and replay["review_id"] == first["review_id"]
    with case.transaction() as database:
        from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
        retained = PostgresGameFeedbackStore(database).get(case.prepared.lapse.key)
        assert retained.status == "settled" and retained.settlement_review_id == first["review_id"]
        assert database.execute_native("SELECT id FROM reviews WHERE id=%s", (case.history_id,)).fetchone()
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'", (case.card_id,)).fetchone()[0] == 1
        assert database.execute_native("SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 1
        assert retained.available_by.isoformat() == admitted.available_by


def test_producer_absence_does_not_block_study(native_case, monkeypatch):
    case = native_case
    with case.transaction() as database:
        submit_feedback(case.prepared, _native_owner(case, database))
    import builtins
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name.startswith(("app.games", "app.repertoire", "app.product_composition")):
            raise AssertionError("ordinary Study tried to load an optional producer: " + name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    with case.transaction() as database:
        entry = _issue(database, case.card_id)
        assert _ordinary_review(database, case.card_id, entry, "absent:" + case.identity)["persisted"]
    with case.transaction() as database:
        assert _native_owner(case, database).effect_status(case.prepared.lapse.key).status == "settled"


def test_no_study_day_and_restart_preserve_deadline(native_case):
    case = native_case
    with case.transaction() as database:
        first = submit_feedback(case.prepared, _native_owner(case, database))
        database.execute_native("DELETE FROM daily_queue WHERE card_id=%s", (case.card_id,))
    with case.transaction() as database:
        from app.services.queue_application import seed_queue
        future_day = (NOW+timedelta(days=3)).date().isoformat()
        seed_queue(database, future_day)
        current = _native_owner(case, database, NOW+timedelta(days=3)).effect_status(case.prepared.lapse.key)
        assert current.available_by == first.available_by
        assert database.execute_native("""SELECT 1 FROM daily_queue
            WHERE card_id=%s AND queue_date=%s AND status='queued'""", (case.card_id, future_day)).fetchone()


def test_unrelated_active_attempt_unchanged(native_case):
    case = native_case
    with case.transaction() as database:
        active = case.card_id + "-active"
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,stability,content_type,trained_color)
            SELECT %s,repertoire_id,kind,start_fen,'["d2d4"]',state,due_date,introduced_at,revision,
                interval_days,fsrs_card_json,stability,content_type,trained_color FROM cards WHERE id=%s""", (active, case.card_id))
        database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('white',%s)", (active,))
        database.execute_native("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES(%s,%s,0,'review')", (NOW.date().isoformat(), active))
        entry = _issue(database, active)
        before_card = tuple(database.execute_native("SELECT * FROM cards WHERE id=%s", (active,)).fetchone())
        before_origin = tuple(database.execute_native("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s", (entry,)).fetchone())
        submit_feedback(case.prepared, _native_owner(case, database))
        assert tuple(database.execute_native("SELECT * FROM cards WHERE id=%s", (active,)).fetchone()) == before_card
        assert tuple(database.execute_native("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s", (entry,)).fetchone()) == before_origin
        assert database.execute_native("SELECT position FROM daily_queue WHERE id=%s", (entry,)).fetchone()[0] == 0


@pytest.fixture
def fenced_case(native_case):
    from test_product_game_lapse_postgres import retained_owner_case
    from app.product_composition.feedback_commands import prepare_captured_feedback
    case = native_case
    case.lapse = case.prepared.lapse
    _, capture, _, _ = retained_owner_case(case, False)
    prepared = prepare_captured_feedback(capture, detected_at=NOW.isoformat(), occurrence_ply=1)
    return case, prepared


def test_retained_source_fence_to_normal_study_controller(fenced_case):
    from app.product_composition.feedback_commands import receive_imported_feedback
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case, prepared = fenced_case
    with case.transaction() as database:
        first = receive_imported_feedback(database, prepared, clock=lambda: NOW)
        assert first["status"] == "applied"
    with case.transaction() as database:
        repeated = receive_imported_feedback(database, prepared, clock=lambda: NOW+timedelta(days=1))
        assert repeated["outcome"] == "duplicate" and repeated["available_by"] == first["available_by"]
        entry = _issue(database, case.card_id)
        reviewed = _ordinary_review(database, case.card_id, entry, "fenced:" + case.identity)
    with case.transaction() as database:
        effect = PostgresGameFeedbackStore(database).get(prepared.feedback.lapse.key)
        assert effect.status == "settled" and effect.settlement_review_id == reviewed["review_id"]
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'", (case.card_id,)).fetchone()[0] == 1


def test_changed_canonical_source_cannot_apply_retained_feedback(fenced_case):
    from app.product_composition.feedback_commands import receive_imported_feedback
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentDecisionUnavailable
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case, prepared = fenced_case
    with case.transaction() as database:
        database.execute_native("UPDATE imported_games SET content_hash=%s WHERE id=%s",
            ("e"*64, prepared.capture["game"]["id"]))
    with pytest.raises(CurrentDecisionUnavailable, match="canonical_imported_source_changed"):
        with case.transaction() as database:
            receive_imported_feedback(database, prepared, clock=lambda: NOW)
    with case.transaction() as database:
        assert PostgresGameFeedbackStore(database).get(prepared.feedback.lapse.key) is None
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'", (case.card_id,)).fetchone()[0] == 0


def test_pending_effect_resumes_with_durable_cursor_without_producer(fenced_case, monkeypatch):
    import builtins
    from app.product_composition.feedback_commands import receive_imported_feedback
    from app.product_composition.feedback_maintenance import KIND, execute_feedback_sweep
    from app.services.durable_tasks import enqueue_task_in_transaction
    from tempo_legacy_database.adapters.durable_tasks import storage
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    from app.study.game_feedback import effect_id
    case, prepared = fenced_case
    with case.transaction() as database:
        database.execute_native("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES(%s,%s,0,'review')",
            (NOW.date().isoformat(),case.card_id))
        entry = _issue(database,case.card_id)
        pending = receive_imported_feedback(database,prepared,clock=lambda:NOW)
        assert pending["status"] == "pending"
        _ordinary_review(database,case.card_id,entry,"again:"+case.identity,outcome="again")
        queued = enqueue_task_in_transaction(database,KIND,case.identity,{"after":""},priority=15)
        token = uuid.uuid4().hex
        storage.claim_lease(database,(token,(NOW+timedelta(days=2)).isoformat(),NOW.isoformat(),NOW.isoformat(),queued["id"],queued["generation"]))
        claimed = dict(storage.task_by_id(database,(queued["id"],)))
    original = builtins.__import__
    def absent(name,*args,**kwargs):
        if name.startswith("app.games"):
            raise AssertionError("Study maintenance tried to load optional Games")
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",absent)
    assert execute_feedback_sweep(claimed,read=case.transaction,write=case.transaction,
        capture=lambda *args:prepared.capture,clock=lambda:NOW+timedelta(hours=2)) is False
    with case.transaction() as database:
        effect=PostgresGameFeedbackStore(database).get(prepared.feedback.lapse.key)
        assert effect.status == "applied" and effect.available_by.isoformat() == pending["available_by"]
        task=dict(storage.task_by_id(database,(queued["id"],)))
        assert json.loads(task["payload_json"])["after"] == effect_id(effect.key)
        assert task["state"] == "queued"
    # A stale execution cannot replay its effect or advance another generation.
    assert execute_feedback_sweep(claimed,read=case.transaction,write=case.transaction,
        capture=lambda *args:prepared.capture,clock=lambda:NOW+timedelta(hours=3)) is False
    with case.transaction() as database:
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",(case.card_id,)).fetchone()[0] == 1
