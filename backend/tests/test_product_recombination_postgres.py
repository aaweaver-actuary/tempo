"""T22 private-store native contract, not ordinary Study/browser acceptance.

Requires the explicitly task-owned TEMPO_RECOMBINATION_TEST_DSN. The caller
starts a bounded labelled disposable server from an already installed image.
The fixture refuses other database names, creates/drops only its own schema,
and never applies production migrations or changes a retained Study table.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
import os
from threading import Barrier
from uuid import uuid4
from pathlib import Path
import hashlib
import importlib.util

import psycopg
from psycopg import sql
import pytest

from app.contracts.product_v02.presentation import Presentation
from app.study.recombination.models import CleanStreak, ComponentRef, JoinMember, ParentConnection, RouteContext, RouteUse
from app.study.recombination.ports import CurrentRoute, Obligation
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.study.recombination import PostgresRecombinationStore, SCHEMA, _event, _json
from test_product_recombination_application import StudyFixture, earn, fixture
from test_product_recombination_policy import FrozenRoute, REVIEW_TIME, review


def decode_current(payload):
    def route(value):
        return FrozenRoute(**(value | {"moves":tuple(value["moves"])}))
    def ref(value):
        return ComponentRef(**(value | {"route_use":RouteUse(**value["route_use"])}))
    def state(value):
        return CleanStreak(**(value | {"last_event_at":datetime.fromisoformat(value["last_event_at"])
            if value["last_event_at"] else None}))
    context = dict(payload["context"])
    context.update(route=route(context["route"]), authored_routes=tuple(route(r) for r in context["authored_routes"]),
        parent_connections=tuple(ParentConnection(ref(c["parent"]), ref(c["child"])) for c in context["parent_connections"]))
    members = []
    for value in payload["members"]:
        values = value | dict(presentation=Presentation.model_validate_json(json.dumps(value["presentation"])),
            streak=state(value["streak"]), route_uses=tuple(RouteUse(**u) for u in value["route_uses"]), moves=tuple(value["moves"]))
        members.append(JoinMember(**values))
    return CurrentRoute(RouteContext(**context), tuple(members),
        tuple((i, datetime.fromisoformat(d)) for i, d in payload["due"]),
        tuple(Obligation(**(o | {"available_by":datetime.fromisoformat(o["available_by"])})) for o in payload["obligations"]))


class PgStudyFixture(StudyFixture):
    """Native fake boundary; actual retained owner wiring is intentionally absent."""
    def __init__(self, database):
        super().__init__()
        self.database = database
        self.saved = SavedEvents(database)

    def try_current(self, route_id):
        row = self.database.execute_native("SELECT payload FROM fixture_current WHERE route_id=%s FOR UPDATE SKIP LOCKED",
                                            (route_id,)).fetchone()
        if not row:
            return None
        self.current = decode_current(row[0])
        return self.current

    def write_current(self):
        self.database.execute_native("UPDATE fixture_current SET payload=%s::jsonb WHERE route_id=%s",
            (_json(asdict(self.current)), self.current.context.route.route_id))

    def saved_review(self, event):
        return self.saved.get(event.event_id) == event

    def identity_available(self, identifier):
        return identifier not in {"A","B","C"} and not self.database.execute_native(
            "SELECT 1 FROM fixture_initialization WHERE presentation_id=%s", (identifier,)).fetchone()

    def apply(self, change):
        super().apply(change)
        for result in change.results:
            # This is a fixture of the required existing-scheduler call, NOT a
            # parallel product scheduler. No historical reviews/FSRS values.
            self.database.execute_native("""INSERT INTO fixture_initialization
                (presentation_id,due_at,introduced,fsrs_state,review_count,quota_charge)
                VALUES(%s,%s,true,NULL,0,0)""", (result.presentation.presentation_id, result.due_at))
        self.write_current()


class SavedEvents:
    def __init__(self, database):
        self.database = database

    def __setitem__(self, key, event):
        prior = self.get(key)
        assert prior is None or prior == event
        self.database.execute_native("INSERT INTO fixture_saved_events(event_id,event_json) VALUES(%s,%s::jsonb) ON CONFLICT DO NOTHING",
            (key, _json(asdict(event))))

    def get(self, key):
        row = self.database.execute_native("SELECT event_json FROM fixture_saved_events WHERE event_id=%s", (key,)).fetchone()
        return _event(row[0]) if row else None


@pytest.fixture
def transaction():
    dsn = os.environ.get("TEMPO_RECOMBINATION_TEST_DSN")
    if not dsn:
        pytest.fail("task-owned TEMPO_RECOMBINATION_TEST_DSN is required; no native pass claimed")
    assert psycopg.conninfo.conninfo_to_dict(dsn)["dbname"].startswith("tempo_task503_")
    namespace = "recombination_" + uuid4().hex

    @contextmanager
    def connect():
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
            raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(namespace)))
            raw.execute("SET LOCAL lock_timeout='2s'")
            raw.execute("SET LOCAL statement_timeout='4s'")
            yield PostgresConnection(raw)

    with psycopg.connect(dsn) as raw:
        raw.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace)))
        raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(namespace)))
        raw.execute(SCHEMA, prepare=False)
        raw.execute("""CREATE TABLE fixture_current(route_id text PRIMARY KEY, payload jsonb NOT NULL);
            CREATE TABLE fixture_saved_events(event_id text PRIMARY KEY,event_json jsonb NOT NULL);
            CREATE TABLE fixture_initialization(presentation_id text PRIMARY KEY,due_at timestamptz NOT NULL,
                introduced boolean NOT NULL,fsrs_state jsonb,review_count integer NOT NULL,quota_charge integer NOT NULL);
            CREATE TABLE fixture_obligations(logical_id text PRIMARY KEY,decision_id text NOT NULL,available_by timestamptz NOT NULL)""",
            prepare=False)
    with connect() as database:
        current = StudyFixture().current
        owed = Obligation("original-effect-identity", "decision:A:0", REVIEW_TIME - timedelta(hours=1))
        current = replace(current, obligations=(owed,))
        database.execute_native("INSERT INTO fixture_current VALUES(%s,%s::jsonb)", ("main-line", _json(asdict(current))))
        database.execute_native("INSERT INTO fixture_obligations VALUES(%s,%s,%s)", (owed.logical_id, owed.decision_id, owed.available_by))
    try:
        yield connect
    finally:
        with psycopg.connect(dsn) as raw:
            raw.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(namespace)))


def service(database, store_type=PostgresRecombinationStore):
    study = PgStudyFixture(database)
    app, store, _ = fixture(store_type(database), study)
    # Initial fixture display read takes no lock. Application calls try_current
    # itself after its nonblocking route lock; no test holds a row across barrier.
    row = database.execute_native("SELECT payload FROM fixture_current WHERE route_id='main-line'").fetchone()
    study.current = decode_current(row[0])
    return app, store, study


def prepared(transaction):
    with transaction() as database:
        app, _, study = service(database)
        for identifier in ("A","B","C"):
            earn(app, study, identifier)
        return app.propose("main-line").plan


def test_native_rollback_after_initialization_retains_complete_old_state(transaction):
    plan = prepared(transaction)
    class FailAfterSave(PostgresRecombinationStore):
        def save_change(self, *args):
            super().save_change(*args)
            raise RuntimeError("injected_before_commit")
    with pytest.raises(RuntimeError, match="injected_before_commit"):
        with transaction() as database:
            service(database, FailAfterSave)[0].apply_join(plan)
    with transaction() as database:
        app, store, study = service(database)
        assert len(study.current.members) == 3
        for table in ("study_recombination_changes", "study_recombination_presentations", "study_recombination_current", "fixture_initialization"):
            assert database.execute_native(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] == 0
        assert app.apply_join(plan).status == "applied"
    with transaction() as database:
        app, store, study = service(database)
        assert len(study.current.members) == 2 and store.receipt(plan.plan_id)[1].status == "applied"


def test_native_concurrent_acceptance_creates_one_composite_and_one_receipt(transaction):
    plan, barrier = prepared(transaction), Barrier(2)
    def accept():
        with transaction() as database:
            app, _, _ = service(database)
            barrier.wait(timeout=5)
            return app.apply_join(plan)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: accept(), range(2)))
    assert any(r.status == "applied" for r in results)
    assert all(r.status in {"applied","active_attempt"} for r in results)
    with transaction() as database:
        app, _, _ = service(database)
        receipt = app.apply_join(plan)
        assert receipt.status == "applied"
        for table in ("study_recombination_changes","study_recombination_presentations","fixture_initialization"):
            assert database.execute_native(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] == 1


def test_native_recreated_owner_reads_streak_lineage_deadline_without_manufactured_history(transaction):
    plan = prepared(transaction)
    with transaction() as database:
        service(database)[0].apply_join(plan)
    with transaction() as database:
        app, store, study = service(database)
        combined = next(m for m in study.current.members if m.presentation.presentation_id == plan.result.presentation_id)
        assert store.streak(combined.streak).count == 0
        assert {"A","B"}.issubset(combined.presentation.lineage)
        original = database.execute_native("SELECT logical_id,decision_id,available_by FROM fixture_obligations").fetchone()
        initialized = database.execute_native("SELECT due_at,introduced,fsrs_state,review_count,quota_charge FROM fixture_initialization").fetchone()
        assert original[0] == "original-effect-identity" and initialized[0] == original[2]
        assert tuple(initialized)[1:] == (True,None,0,0)
        assert store.current_presentation("main-line", original[1]) == combined.presentation.presentation_id
        assert store.current_presentation("other-route", original[1]) is None
        assert database.execute_native("SELECT count(*) FROM study_recombination_completions WHERE presentation_id=%s",
                                       (combined.presentation.presentation_id,)).fetchone()[0] == 0
        assert app.propose("main-line").plan is None


def test_native_pending_old_review_is_never_relabelled(transaction):
    plan = prepared(transaction)
    with transaction() as database:
        app, _, study = service(database)
        study.current = replace(study.current, members=tuple(replace(m, pending_attempt=True)
            if m.presentation.presentation_id == "A" else m for m in study.current.members))
        study.write_current()
        assert app.apply_join(plan).status == "active_attempt"
    with transaction() as database:
        app, _, study = service(database)
        original = review(4)
        study.saved[original.event_id] = original
        study.current = replace(study.current, members=tuple(replace(m, pending_attempt=False)
            if m.presentation.presentation_id == "A" else m for m in study.current.members))
        study.write_current()
        app.record_review("main-line", original)
        assert app.apply_join(plan).status == "applied"
    with transaction() as database:
        app, store, study = service(database)
        original = study.saved.get("A:event:4")
        app.record_review("main-line", original)
        assert store.event(original.event_id) == original
        assert original.presentation_id == "A"
        combined = next(m for m in study.current.members if m.presentation.presentation_id == plan.result.presentation_id)
        assert store.streak(combined.streak).count == 0


def test_native_repeated_split_rejoin_retains_distinct_generations_and_receipts(transaction):
    plan = prepared(transaction)
    with transaction() as database:
        service(database)[0].apply_join(plan)
    original = plan.result.presentation_id
    for generation in range(2):
        with transaction() as database:
            app, _, study = service(database)
            combined = next(m for m in study.current.members if m.presentation.presentation_id == original)
            earn(app, study, original, start=10+generation*20, outcome="again", epoch=combined.streak.evidence_epoch)
            command = f"split:{generation}"
            receipt = app.accept_split("main-line", original, 1, command, confirmed=True)
            assert receipt.status == "applied"
            assert app.accept_split("main-line", original, 1, command, confirmed=True) == receipt
        with transaction() as database:
            app, _, study = service(database)
            assert app.propose("main-line").plan is None
            fragments = tuple(m for m in study.current.members if original in m.presentation.lineage)
            for fragment in fragments:
                earn(app, study, fragment.presentation.presentation_id, start=20+generation*20, epoch=fragment.streak.evidence_epoch)
            rejoin = app.propose("main-line").plan
            assert rejoin.result.presentation_id != original
            assert app.apply_join(rejoin).status == "applied"
            original = rejoin.result.presentation_id
    with transaction() as database:
        app, store, study = service(database)
        assert database.execute_native("SELECT count(*) FROM study_recombination_changes").fetchone()[0] == 5
        assert database.execute_native("SELECT count(*) FROM study_recombination_presentations").fetchone()[0] == 7
        assert database.execute_native("SELECT count(*) FROM study_recombination_completions").fetchone()[0] == 27
        assert database.execute_native("SELECT count(*) FROM fixture_obligations").fetchone()[0] == 1
        assert app.apply_join(plan).status == "applied"  # original receipt, no resurrection


def test_native_shared_evidence_identity_serializes_different_routes_without_waiting(transaction):
    def record_other_route():
        with transaction() as database:
            app, _, study = service(database)
            event = review(1)
            study.saved[event.event_id] = event
            return app.record_review("other-shared-route", event)
    with transaction() as database:
        app, _, study = service(database)
        event = review(0)
        study.saved[event.event_id] = event
        assert app.record_review("main-line", event).count == 1
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(record_other_route)
            with pytest.raises(RuntimeError, match="presentation_busy"):
                future.result(timeout=5)
    assert record_other_route().count == 2
    with transaction() as database:
        _, store, _ = service(database)
        assert store.streak(CleanStreak("A",1,"initial")).count == 2
        assert database.execute_native("SELECT count(*) FROM study_recombination_completions").fetchone()[0] == 2


@pytest.fixture
def current_owner_transaction(monkeypatch):
    """Actual retained owner schema on a separate DB in the task's own server.

    Apply the complete ordered source DDL through allocated T22 migration 053.
    Runtime readiness registration/browser installation is separately qualified.
    """
    from tempo_legacy_database.adapters import postgres_store
    monkeypatch.setattr(postgres_store,"configured",lambda:True)
    dsn = os.environ.get("TEMPO_RECOMBINATION_TEST_DSN")
    if not dsn:
        pytest.fail("explicit task-owned native DSN required")
    options = psycopg.conninfo.conninfo_to_dict(dsn)
    assert options["dbname"].startswith("tempo_task503_")
    name = "tempo_task503_owner_" + uuid4().hex
    with psycopg.connect(dsn,autocommit=True) as raw:
        raw.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    owner_dsn = psycopg.conninfo.make_conninfo(dsn,dbname=name)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("recombination_migration_owner",root/"database/tooling/apply_postgres_migrations.py")
    migration_owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_owner)
    try:
        with psycopg.connect(owner_dsn) as raw:
            migrations = sorted(migration_owner.MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
            migration_owner.validate_migration_history(migrations,[],expected_version=53)
            for path in migrations:
                registration = migration_owner.MODULE_MIGRATIONS.get(path.name)
                if registration:
                    source = (root/"database"/registration[2]).read_bytes()
                    assert hashlib.sha256(source).hexdigest()==registration[3]
                    raw.execute(source.decode(),prepare=False)
                raw.execute(path.read_text(),prepare=False)
                assert raw.execute("SELECT max(version) FROM tempo_schema_migrations").fetchone()[0]==int(path.name[:3])
            raw.execute("INSERT INTO settings(id,timezone,new_cards_per_day,study_new_per_day,tactics_new_per_day) VALUES(1,'UTC',0,0,0)")
        @contextmanager
        def connect():
            with psycopg.connect(owner_dsn,row_factory=tempo_row_factory) as raw:
                raw.execute("SET LOCAL lock_timeout='2s'")
                raw.execute("SET LOCAL statement_timeout='5s'")
                yield PostgresConnection(raw)
        with connect() as database:
            seed_current_owner(database)
        yield connect
    finally:
        with psycopg.connect(dsn,autocommit=True) as raw:
            raw.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def seed_current_owner(database):
    from fixtures.product_recombination import seed_current_owner as seed
    return seed(database)


def ordinary_current_review(database, card_id, monkeypatch, *, outcome="correct", attempt_id=None, hint_after_response=False):
    """Actual issuance, immutable manifest, evidence, scheduler and save wrapper."""
    from app.queue_commands import issue_queue_head
    from app.services.review_service import ensure_card_queued_after
    from app.services.opening_decision_evidence import decision_manifest
    from app.services import learning_review_adapter
    from tempo_legacy_database.adapters.opening_evidence.storage import queue_context_rows
    now, attempt = datetime.now().astimezone(), attempt_id or "r11-"+uuid4().hex
    day = now.date().isoformat()
    ensure_card_queued_after(database,card_id,0,attempt_state="clean",queue_date=day,preserve_order=False)
    entry = database.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND queue_date=%s AND status='queued' ORDER BY cycle DESC LIMIT 1",(card_id,day)).fetchone()[0]
    assert issue_queue_head(database,dict(entry_id=entry,card_id=card_id,expected_revision=1,queue_date=day))["issued"]
    snapshot = dict(queue_context_rows(database,([entry],))[0])
    manifest = decision_manifest(snapshot,snapshot["repertoire_id"])
    events = []
    for index,decision in enumerate(manifest["decisions"]):
        events.append(dict(sequence=index+1,decision_index=index,decision_id=decision["decision_id"],
            expected_uci=decision["expected_uci"],kind="first_response",observed_at=now.isoformat(),
            response_uci=decision["expected_uci"],assistance=None,disposition="expected"))
    if hint_after_response:
        decision = manifest["decisions"][-1]
        events.append(dict(sequence=len(events)+1,decision_index=decision["decision_index"],decision_id=decision["decision_id"],
            expected_uci=decision["expected_uci"],kind="assistance",observed_at=now.isoformat(),response_uci=None,assistance="hint",disposition=None))
    checkpoint = dict(attempt_id=attempt,manifest=manifest,origin_queue_entry_id=entry,queue_entry_id=entry,
        started_at=now.isoformat(),study_timezone="UTC",events=events,
        terminal=dict(state="complete",final_sequence=len(events),ended_at=now.isoformat()))
    # Only inject the task DB's actual policy read; no authority, grade, scheduler
    # result, review receipt, completion proof or queue operation is faked.
    from contextlib import nullcontext
    monkeypatch.setattr(learning_review_adapter,"read_connection",lambda:nullcontext(database))
    payload = dict(card_id=card_id,prepared_manifest=manifest,review=dict(outcome=outcome,guided=False,
        queue_entry_id=entry,expected_revision=1,attempt_id=attempt,opening_evidence_completion=checkpoint))
    return learning_review_adapter.submit_legacy_review(database,payload),payload


def test_current_owner_normal_reviews_join_after_save_and_require_fresh_combined_evidence(current_owner_transaction,monkeypatch):
    from tempo_legacy_database.adapters.study.recombination import CurrentStudyRecombination
    transaction = current_owner_transaction
    for identifier in ("A","C","B"):
        for _ in range(3):
            with transaction() as database:
                result,_ = ordinary_current_review(database,identifier,monkeypatch)
    assert result["recombination"]["status"]=="applied"
    combined = result["recombination"]["resulting_presentation_id"]
    with transaction() as database:
        row = database.execute_native("SELECT fsrs_card_json,repetitions,introduced_at,archived FROM cards WHERE id=%s",(combined,)).fetchone()
        assert row[0] is None and row[1]==0 and row[2]==datetime.now().astimezone().date().isoformat() and row[3]==0
        assert database.execute_native("SELECT count(*) FROM reviews WHERE card_id=%s",(combined,)).fetchone()[0]==0
        assert database.execute_native("SELECT count(*) FROM cards WHERE id IN ('A','B','C') AND archived=0 AND superseded_by IS NULL").fetchone()[0]==3
        boundary = CurrentStudyRecombination(database)
        current = boundary.try_current("r11-route")
        assert current is not None,boundary.reason
        app,_,_ = fixture(PostgresRecombinationStore(database),boundary)
        assert app.propose("r11-route").plan is None
    for _ in range(3):
        with transaction() as database:
            result,_ = ordinary_current_review(database,combined,monkeypatch)
    assert result["recombination"]["status"]=="applied"
    assert result["recombination"]["resulting_presentation_id"]!=combined
    with transaction() as database:
        assert database.execute_native("SELECT count(*) FROM study_recombination_changes").fetchone()[0]==2
        assert database.execute_native("SELECT count(*) FROM reviews WHERE card_id=%s",(combined,)).fetchone()[0]==3
        assert database.execute_native("SELECT count(*) FROM reviews WHERE source_ref='unknown-historical-assistance'").fetchone()[0]==1


def qualify_before_final_review(transaction,monkeypatch):
    for identifier,count in (("A",3),("C",3),("B",2)):
        for _ in range(count):
            with transaction() as database:
                ordinary_current_review(database,identifier,monkeypatch)


def test_current_owner_join_failure_rolls_back_only_regrouping_and_keeps_saved_attempt(current_owner_transaction,monkeypatch):
    transaction = current_owner_transaction
    qualify_before_final_review(transaction,monkeypatch)
    original = PostgresRecombinationStore.save_change
    def fail_after_all_writes(self,*args):
        original(self,*args)
        raise RuntimeError("injected_after_real_owner_initialization")
    with monkeypatch.context() as patch:
        patch.setattr(PostgresRecombinationStore,"save_change",fail_after_all_writes)
        with transaction() as database:
            result,payload = ordinary_current_review(database,"B",monkeypatch)
            assert result["persisted"] and result["recombination"]["reason"]=="join_rolled_back:RuntimeError"
    with transaction() as database:
        assert database.execute_native("SELECT count(*) FROM cards WHERE source_ref LIKE %s",('recombination:%',)).fetchone()[0]==0
        assert database.execute_native("SELECT count(*) FROM study_recombination_changes").fetchone()[0]==0
        assert database.execute_native("SELECT card_id FROM review_attempt_receipts WHERE attempt_id=%s",(payload["review"]["attempt_id"],)).fetchone()[0]=="B"
        from app.services.learning_review_adapter import submit_legacy_review
        from contextlib import nullcontext
        monkeypatch.setattr("app.services.learning_review_adapter.read_connection",lambda:nullcontext(database))
        replay = submit_legacy_review(database,payload)
        assert replay["review_id"]==result["review_id"] and replay["recombination"]["status"]=="applied"
        assert database.execute_native("SELECT count(*) FROM reviews WHERE card_id='B'").fetchone()[0]==3


def test_current_owner_active_member_defers_without_changing_original_attempt(current_owner_transaction,monkeypatch):
    from app.queue_commands import issue_queue_head
    from app.services.review_service import ensure_card_queued_after
    transaction = current_owner_transaction
    qualify_before_final_review(transaction,monkeypatch)
    day = datetime.now().astimezone().date().isoformat()
    with transaction() as database:
        # Make C a genuinely shared member. Its other membership must remain
        # valid; the retained queue cannot safely join that occurrence. This
        # leaves only the planned A+B pair, whose A is actually issued below.
        database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('r11-shared','Other use','fixture',%s)",(datetime.now().astimezone().isoformat(),))
        database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('r11-shared','C',0)")
        ensure_card_queued_after(database,"A",0,queue_date=day,preserve_order=False)
        entry = database.execute_native("SELECT id FROM daily_queue WHERE card_id='A' AND status='queued' ORDER BY cycle DESC LIMIT 1").fetchone()[0]
        assert issue_queue_head(database,dict(entry_id=entry,card_id="A",expected_revision=1,queue_date=day))["issued"]
        before = tuple(database.execute_native("SELECT card_id,revision,start_fen,moves_json,issued_as_head,review_result_json FROM queue_attempt_origins WHERE queue_entry_id=%s",(entry,)).fetchone())
    with transaction() as database:
        result,_ = ordinary_current_review(database,"B",monkeypatch)
        assert result["recombination"]["status"]=="held"
    with transaction() as database:
        assert database.execute_native("SELECT count(*) FROM study_recombination_changes").fetchone()[0]==0
        assert tuple(database.execute_native("SELECT card_id,revision,start_fen,moves_json,issued_as_head,review_result_json FROM queue_attempt_origins WHERE queue_entry_id=%s",(entry,)).fetchone())==before
        assert database.execute_native("SELECT status FROM daily_queue WHERE id=%s",(entry,)).fetchone()[0]=="queued"
        assert database.execute_native("SELECT 1 FROM repertoire_cards WHERE repertoire_id='r11-shared' AND card_id='C'").fetchone()


def test_current_owner_obligation_target_split_and_offday_readback_preserve_original_history(current_owner_transaction,monkeypatch):
    """Seed an existing owed receipt at the join boundary, then use real owners.

    Its previously applied lapse is an explicit synthetic source fixture; lapse
    scheduling is unchanged #487 evidence. No Study success/settlement is seeded.
    """
    from app.study.game_feedback import effect_id
    from app.contracts.product_v02.learning import GameLapse
    from test_product_game_lapse import fixture as lapse_fixture
    from tempo_game_evidence_contracts.legacy import decision_identity
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore, resume_available_feedback
    from app.services.queue_application import seed_queue
    transaction = current_owner_transaction
    qualify_before_final_review(transaction,monkeypatch)
    now = datetime.now().astimezone()
    lapse = lapse_fixture("lapse",GameLapse)
    decision = decision_identity("A",0,[])
    lapse = lapse.model_copy(update={"key":lapse.key.model_copy(update={"decision_id":decision}),
        "finding":lapse.finding.model_copy(update={"decision_id":decision,"presentation_id":"A","repertoire_id":"r11-repertoire"})})
    logical, deadline = effect_id(lapse.key),now-timedelta(hours=1)
    with transaction() as database:
        review_id = database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            source_kind,source_ref) VALUES('A','again',%s,10,0,'game_lapse',%s) RETURNING id""",
            ((now-timedelta(days=2)).isoformat(),logical)).fetchone()[0]
        database.execute_native("""INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,payload_json,payload_hash,
            card_id,card_revision,decision_id,received_at,detected_at,available_by,status,applied_at,review_id,updated_at)
            VALUES(%s,%s,'1',%s,%s,'A',1,%s,%s,%s,%s,'applied',%s,%s,%s)""", (logical,lapse.key.model_dump_json(),
            lapse.model_dump_json(),"a"*64,decision,now-timedelta(days=2),now-timedelta(days=2),deadline,now-timedelta(days=2),review_id,now))
        result,_ = ordinary_current_review(database,"B",monkeypatch)
        assert result["recombination"]["status"]=="applied"
        combined = result["recombination"]["resulting_presentation_id"]
        original = PostgresGameFeedbackStore(database).get(lapse.key)
        assert original.card_id=="A" and original.available_by==deadline and original.status=="applied"
    with transaction() as database:
        day = now.date().isoformat()
        seed_queue(database,day)
        resume_available_feedback(database,day)
        assert database.execute_native("SELECT 1 FROM daily_queue WHERE card_id=%s AND status='queued'",(combined,)).fetchone()
        assert not database.execute_native("SELECT 1 FROM daily_queue WHERE card_id IN ('A','B') AND status='queued'").fetchone()
        assert PostgresGameFeedbackStore(database).get(lapse.key).available_by==deadline
    with transaction() as database:
        reviewed,_ = ordinary_current_review(database,combined,monkeypatch)
        settled = PostgresGameFeedbackStore(database).get(lapse.key)
        assert settled.status=="settled" and settled.settlement_review_id==reviewed["review_id"] and settled.available_by==deadline
    for _ in range(3):
        with transaction() as database:
            ordinary_current_review(database,combined,monkeypatch,outcome="again")
    with transaction() as database:
        from app.prefix_split_commands import accept_prefix_split
        before = tuple(tuple(r) for r in database.execute_native("SELECT id,card_id,source_kind,source_ref FROM reviews ORDER BY id").fetchall())
        split = accept_prefix_split(database,dict(card_id=combined,request=dict(expected_revision=1)))
        assert split["applied"]
        assert accept_prefix_split(database,dict(card_id=combined,request=dict(expected_revision=1)))["idempotent"]
        assert tuple(tuple(r) for r in database.execute_native("SELECT id,card_id,source_kind,source_ref FROM reviews ORDER BY id").fetchall())==before
        assert database.execute_native("SELECT count(*) FROM reviews WHERE source_kind='game_lapse'").fetchone()[0]==1
        for fragment in (split["parent"]["card_id"],split["continuation"]["card_id"]):
            row = database.execute_native("SELECT fsrs_card_json,repetitions,introduced_at,archived FROM cards WHERE id=%s",(fragment,)).fetchone()
            assert row[0] is None and row[1]==0 and row[2] is not None and row[3]==0
        assert tuple(database.execute_native("SELECT generation,state FROM background_tasks WHERE id='r11-graph'").fetchone())==(1,"complete")


def test_current_owner_any_saved_assistance_resets_and_invalidated_success_cannot_qualify(current_owner_transaction,monkeypatch):
    transaction = current_owner_transaction
    for _ in range(2):
        with transaction() as database:
            ordinary_current_review(database,"A",monkeypatch)
    with transaction() as database:
        result,_ = ordinary_current_review(database,"A",monkeypatch,hint_after_response=True)
        assert result["recombination"]["clean_streak"]==0
        state = PostgresRecombinationStore(database).streak(CleanStreak("A",1,"physical:A:1"))
        assert state.count==0
    for _ in range(3):
        with transaction() as database:
            ordinary_current_review(database,"A",monkeypatch)
    with transaction() as database:
        database.execute_native("UPDATE reviews SET invalidated_at=%s WHERE id=(SELECT max(id) FROM reviews WHERE card_id='A')",
            (datetime.now().astimezone().isoformat(),))
        assert PostgresRecombinationStore(database,validate_reviews=True).streak(CleanStreak("A",1,"physical:A:1")).count==0
        result,_ = ordinary_current_review(database,"A",monkeypatch)
        assert result["recombination"]["clean_streak"]==1


def test_current_owner_existing_review_continues_when_a_join_neighbour_is_busy(current_owner_transaction,monkeypatch):
    transaction=current_owner_transaction
    qualify_before_final_review(transaction,monkeypatch)
    with transaction() as database:
        result,_=ordinary_current_review(database,'B',monkeypatch)
        combined=result['recombination']['resulting_presentation_id']
    def review_combined():
        with transaction() as database:
            return ordinary_current_review(database,combined,monkeypatch)[0]
    with transaction() as busy:
        busy.execute_native("SELECT pg_advisory_xact_lock(hashtextextended('tempo:card-edit:C',0))")
        busy.execute_native("SELECT id FROM cards WHERE id='C' FOR UPDATE")
        with ThreadPoolExecutor(max_workers=1) as worker:
            result=worker.submit(review_combined).result(timeout=10)
        assert result['persisted'] and result['recombination']['status']=='held'
        assert result['recombination']['clean_streak']==1
    with transaction() as database:
        assert database.execute_native("SELECT count(*) FROM reviews WHERE card_id=%s",(combined,)).fetchone()[0]==1


def test_current_owner_regrouping_admission_read_proves_owned_identity_only(current_owner_transaction,monkeypatch):
    from tempo_legacy_database.adapters.study.recombination import regrouped_card_sql
    transaction=current_owner_transaction
    qualify_before_final_review(transaction,monkeypatch)
    with transaction() as database:
        result,_=ordinary_current_review(database,'B',monkeypatch)
        combined=result['recombination']['resulting_presentation_id']
        database.execute_native("UPDATE cards SET source_ref='recombination:untrusted-prefix' WHERE id='A'")
        predicate=regrouped_card_sql(database,'c.id')
        assert [row[0] for row in database.execute_native('SELECT c.id FROM cards c WHERE '+predicate).fetchall()]==[combined]
        assert regrouped_card_sql(object(),'card.id')=='FALSE'
        with pytest.raises(ValueError,match='unsupported_regrouping_card_reference'):
            regrouped_card_sql(database,'untrusted SQL')


def test_current_owner_regrouping_costs_zero_actual_introduction_slots(current_owner_transaction,monkeypatch):
    """Actual legacy admission acceptance; requires its coordinated R11 hook."""
    from app.services.queue_admission_adapter import QueueAdmissionAdapter
    transaction=current_owner_transaction
    qualify_before_final_review(transaction,monkeypatch)
    day=datetime.now().astimezone().date().isoformat()
    with transaction() as database:
        # An explicit existing earlier due requires verification today. No
        # introduction, completion or combined review is seeded by this fact.
        database.execute_native("UPDATE cards SET due_date=%s WHERE id='A'",(day,))
        result,_=ordinary_current_review(database,'B',monkeypatch)
        combined=result['recombination']['resulting_presentation_id']
        owner=QueueAdmissionAdapter(database)
        counts=owner.introduction_count_page(day,None)
        unseen={row['card_id'] for row in owner.unseen_openings(day)}
        assert sum(count for _,count in counts.counts)==0 and combined not in unseen, (
            'An already-introduced regrouping must not consume allowance or be reset by unseen reconciliation',
            counts,combined in unseen)


def test_current_owner_optional_conflict_guards_recovery_review_and_rebuild(current_owner_transaction,monkeypatch):
    from app.contracts.review_conflicts import ReviewConflict
    from app.contracts.transport.study import ReviewRequest
    from app.queue_commands import issue_queue_head,mark_attempt_failed
    from app.queue_attempt_origins import recover_queue_entry
    from app.services.learning_review_adapter import submit_legacy_review
    from app.services.queue_application import seed_queue
    from app.services.review_service import ensure_card_queued_after
    from app.study.conflicts import Occurrence,SourceFence
    from tempo_legacy_database.adapters.study.conflicts import PostgresConflictStore
    from contextlib import nullcontext
    transaction=current_owner_transaction
    day=datetime.now().astimezone().date().isoformat()
    with transaction() as database:
        database.execute_native((Path(__file__).resolve().parents[2]/'database/tempo_legacy_database/ddl/study_conflicts.sql').read_text())
        ensure_card_queued_after(database,'A',0,queue_date=day,preserve_order=False)
        entry=database.execute_native("SELECT id FROM daily_queue WHERE card_id='A' AND status='queued'").fetchone()[0]
        assert issue_queue_head(database,dict(entry_id=entry,card_id='A',expected_revision=1,queue_date=day))['issued']
        origin=tuple(database.execute_native("SELECT card_id,revision,start_fen,moves_json,issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s",(entry,)).fetchone())
        # Existing validated hold is a synthetic owner-state fixture. #487's
        # command/source-authority proofs are reused, not faked as C acceptance.
        occurrence=Occurrence('r11-repertoire','r11-route',0,'A',1)
        store=PostgresConflictStore(database)
        store.hold(occurrence,SourceFence('fixture-current',1,'fixture-digest'),True,datetime.now().astimezone())
        store.suspend(occurrence)
    with transaction() as database:
        request=ReviewRequest(outcome='correct',guided=False,queue_entry_id=entry,expected_revision=1,attempt_id='held-original')
        with pytest.raises(ReviewConflict):
            recover_queue_entry(database,'A',request)
        with pytest.raises(ReviewConflict):
            mark_attempt_failed(database,dict(entry_id=entry,card_id='A',expected_revision=1))
        monkeypatch.setattr('app.services.learning_review_adapter.read_connection',lambda:nullcontext(database))
        with pytest.raises(ReviewConflict):
            submit_legacy_review(database,dict(card_id='A',review=request.model_dump()))
        assert not issue_queue_head(database,dict(entry_id=entry,card_id='A',expected_revision=1,queue_date=day))['issued']
        assert database.execute_native("SELECT count(*) FROM review_attempt_receipts").fetchone()[0]==0
        assert database.execute_native("SELECT attempt_failed FROM daily_queue WHERE id=%s",(entry,)).fetchone()[0]==0
        database.execute_native('DELETE FROM daily_queue WHERE id=%s',(entry,))
        seed_queue(database,day)
        assert not database.execute_native("SELECT 1 FROM daily_queue WHERE card_id='A' AND status='queued'").fetchone()
        assert tuple(database.execute_native("SELECT card_id,revision,start_fen,moves_json,issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s",(entry,)).fetchone())==origin
    with transaction() as database:
        assert ordinary_current_review(database,'B',monkeypatch)[0]['persisted']
