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

import chess
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
            raw.execute("INSERT INTO settings(id,timezone,new_cards_per_day,study_new_per_day,tactics_new_per_day) VALUES(1,'local',0,0,0)")
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


def ordinary_current_review(database, card_id, monkeypatch, *, outcome="correct", attempt_id=None, hint_after_response=False, recorded=False, prepared_next=None):
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
    if recorded:
        payload['review']['recorded_at']=now.isoformat()
    if prepared_next:
        payload['review']['prepared_next']=prepared_next
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


def test_current_owner_recorded_review_with_unrelated_prepared_head_can_join(current_owner_transaction,monkeypatch):
    from app.queue_commands import issue_queue_head
    from app.services.queue_application import seed_queue
    from app.services.review_service import ensure_card_queued_after
    from fixtures.product_recombination import seed_legacy_introduction_history
    transaction=current_owner_transaction
    identifier='product-recombination-native-protected'
    now=datetime.now().astimezone()
    day=now.date().isoformat()
    with transaction() as database:
        seed_legacy_introduction_history(database)
        assert database.execute_native('SELECT count(*) FROM study_recombination_completions').fetchone()[0]==0
        database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('r11-protected','Other','fixture',%s)",(now.isoformat(),))
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,trained_color,canonical_route_source) VALUES(%s,'r11-protected','response',%s,
            '["d2d4"]','learning',%s,%s,'white',0)""",(identifier,chess.STARTING_FEN,day,(now-timedelta(days=10)).date().isoformat()))
        database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES('r11-protected',%s,0)",(identifier,))
        ensure_card_queued_after(database,identifier,0,queue_date=day,preserve_order=False)
        entry=database.execute_native('SELECT id FROM daily_queue WHERE card_id=%s',(identifier,)).fetchone()[0]
        assert issue_queue_head(database,dict(entry_id=entry,card_id=identifier,expected_revision=1,queue_date=day))['issued']
    candidate=dict(queue_entry_id=entry,card_id=identifier,revision=1,cycle=0,local_date=day)
    for card in ('A','C','B'):
        for _ in range(3):
            with transaction() as database:
                seed_queue(database,day)
                result,_=ordinary_current_review(database,card,monkeypatch,recorded=True,prepared_next=candidate)
    assert result['recombination']['status']=='applied',result['recombination']


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
        from app.services.queue_application import seed_queue,presentation_rows
        assert next(row for row in presentation_rows(database,day) if row['card_id']==combined)['admission_kind']=='review'
        for _ in range(2):
            seed_queue(database,day)
            assert sum(count for _,count in owner.introduction_count_page(day,None).counts)==0
        assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(combined,)).fetchone()[0]==day


def issued_split_fixture(transaction,monkeypatch):
    from app.queue_commands import issue_queue_head
    from app.services.review_service import ensure_card_queued_after
    qualify_before_final_review(transaction,monkeypatch)
    with transaction() as database:
        result,_=ordinary_current_review(database,'B',monkeypatch)
        combined=result['recombination']['resulting_presentation_id']
    for _ in range(3):
        with transaction() as database:
            ordinary_current_review(database,combined,monkeypatch,outcome='again')
    day=datetime.now().astimezone().date().isoformat()
    with transaction() as database:
        ensure_card_queued_after(database,'C',0,queue_date=day,preserve_order=False)
        other=database.execute_native("SELECT id FROM daily_queue WHERE card_id='C' AND status='queued'").fetchone()[0]
        assert issue_queue_head(database,dict(entry_id=other,card_id='C',expected_revision=1,queue_date=day))['issued']
        ensure_card_queued_after(database,combined,0,queue_date=day,preserve_order=False)
        entry=database.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND status='queued'",(combined,)).fetchone()[0]
        assert issue_queue_head(database,dict(entry_id=entry,card_id=combined,expected_revision=1,queue_date=day))['issued']
    return combined,entry,other,day


def persist_split_checkpoint(database,combined,entry,kind=None):
    from app.services.opening_decision_evidence import decision_manifest
    from app.services.postgres_opening_evidence import persist_checkpoint
    from tempo_legacy_database.adapters.opening_evidence.storage import queue_context_rows
    manifest=decision_manifest(dict(queue_context_rows(database,([entry],))[0]),'r11-repertoire')
    now=datetime.now().astimezone().isoformat()
    decision=manifest['decisions'][0]
    events=[] if kind is None else [dict(sequence=1,decision_index=0,decision_id=decision['decision_id'],
        expected_uci=decision['expected_uci'],kind='first_response' if kind=='response' else 'assistance',
        observed_at=now,response_uci=decision['expected_uci'] if kind=='response' else None,
        assistance=None if kind=='response' else 'hint',disposition='expected' if kind=='response' else None)]
    attempt='split-head-'+uuid4().hex
    result=persist_checkpoint(database,dict(prepared_manifest=manifest,checkpoint=dict(attempt_id=attempt,
        manifest=manifest,origin_queue_entry_id=entry,queue_entry_id=entry,started_at=now,
        study_timezone='UTC',events=events)))
    assert result['persisted']
    return attempt


def test_current_owner_confirmed_issued_split_keeps_origins_history_and_zero_slots(current_owner_transaction,monkeypatch):
    from app.prefix_split_commands import accept_prefix_split
    from app.services.queue_admission_adapter import QueueAdmissionAdapter
    from app.services.queue_application import seed_queue,presentation_rows
    transaction=current_owner_transaction
    combined,entry,other,day=issued_split_fixture(transaction,monkeypatch)
    with transaction() as database:
        empty=persist_split_checkpoint(database,combined,entry)
        origin=tuple(database.execute_native('SELECT card_id,revision,start_fen,moves_json,issued_as_head,review_result_json FROM queue_attempt_origins WHERE queue_entry_id=%s',(entry,)).fetchone())
        protected=tuple(database.execute_native('SELECT queue.status,queue.position,origin.* FROM daily_queue queue JOIN queue_attempt_origins origin ON origin.queue_entry_id=queue.id WHERE queue.id=%s',(other,)).fetchone())
        history=tuple(tuple(row) for row in database.execute_native('SELECT id,card_id,rating,source_kind,source_ref FROM reviews ORDER BY id').fetchall())
        payload=dict(card_id=combined,request=dict(expected_revision=1,queue_entry_id=entry))
        result=accept_prefix_split(database,payload)
        assert result['applied'] and accept_prefix_split(database,payload)['idempotent']
        assert database.execute_native('SELECT status FROM daily_queue WHERE id=%s',(entry,)).fetchone()[0]=='buried'
        assert tuple(database.execute_native('SELECT card_id,revision,start_fen,moves_json,issued_as_head,review_result_json FROM queue_attempt_origins WHERE queue_entry_id=%s',(entry,)).fetchone())==origin
        assert tuple(database.execute_native('SELECT queue.status,queue.position,origin.* FROM daily_queue queue JOIN queue_attempt_origins origin ON origin.queue_entry_id=queue.id WHERE queue.id=%s',(other,)).fetchone())==protected
        assert tuple(tuple(row) for row in database.execute_native('SELECT id,card_id,rating,source_kind,source_ref FROM reviews ORDER BY id').fetchall())==history
        assert tuple(database.execute_native('SELECT state,contiguous_sequence,review_id FROM opening_evidence_attempts WHERE attempt_id=%s',(empty,)).fetchone())==('active',0,None)
        fragments=(result['parent']['card_id'],result['continuation']['card_id'])
    tomorrow=(datetime.now().astimezone().date()+timedelta(days=1)).isoformat()
    with transaction() as database:
        # Emulate a lost projection, keeping the immutable original origins.
        database.execute_native('DELETE FROM daily_queue WHERE card_id=ANY(%s)',(list(fragments),))
        for _ in range(2):
            seed_queue(database,tomorrow)
        for identifier in fragments:
            assert tuple(database.execute_native('SELECT introduced_at,fsrs_card_json,repetitions FROM cards WHERE id=%s',(identifier,)).fetchone())==(day,None,0)
            assert next(row for row in presentation_rows(database,tomorrow) if row['card_id']==identifier)['admission_kind']=='review'
        owner=QueueAdmissionAdapter(database)
        assert sum(count for _,count in owner.introduction_count_page(day,None).counts)==0
        assert sum(count for _,count in owner.introduction_count_page(day,None,reviewed_only=True).counts)==0
        assert sum(count for _,count in owner.introduction_count_page(tomorrow,None).counts)==0


@pytest.mark.parametrize('kind',['response','assistance'])
def test_current_owner_confirmed_split_rejects_pending_or_changed_attempt_without_retirement(current_owner_transaction,monkeypatch,kind):
    from app.prefix_split_commands import accept_prefix_split
    from fastapi import HTTPException
    transaction=current_owner_transaction
    combined,entry,other,_=issued_split_fixture(transaction,monkeypatch)
    with transaction() as database:
        for request in (dict(expected_revision=1),dict(expected_revision=1,queue_entry_id=other)):
            with pytest.raises(HTTPException) as error:
                accept_prefix_split(database,dict(card_id=combined,request=request))
            assert error.value.status_code==422
            assert database.execute_native('SELECT status FROM daily_queue WHERE id=%s',(entry,)).fetchone()[0]=='queued'
        attempt=persist_split_checkpoint(database,combined,entry,kind)
        before=tuple(database.execute_native('SELECT state,contiguous_sequence,review_id FROM opening_evidence_attempts WHERE attempt_id=%s',(attempt,)).fetchone())
        with pytest.raises(HTTPException) as error:
            accept_prefix_split(database,dict(card_id=combined,request=dict(expected_revision=1,queue_entry_id=entry)))
        assert error.value.status_code==422 and 'pending_attempt' in str(error.value.detail)
        assert database.execute_native('SELECT status FROM daily_queue WHERE id=%s',(entry,)).fetchone()[0]=='queued'
        assert tuple(database.execute_native('SELECT state,contiguous_sequence,review_id FROM opening_evidence_attempts WHERE attempt_id=%s',(attempt,)).fetchone())==before
        assert database.execute_native('SELECT count(*) FROM study_recombination_changes').fetchone()[0]==1


def test_regrouped_lookup_supports_bounded_reader_without_connection():
    from tempo_legacy_database.adapters.study.recombination import regrouped_presentation_ids
    calls = []
    identifiers = [f'presentation-{index:03}' for index in range(260)]

    def read(statement, parameters):
        calls.append((statement, parameters))
        if 'to_regclass' in statement:
            return [(True,)]
        assert len(parameters) <= 128
        assert statement.count('?') == len(parameters)
        return [(identifier,) for identifier in parameters if identifier.endswith('0')]

    result = regrouped_presentation_ids(None, identifiers + identifiers[:10], read=read)
    assert result == frozenset(identifier for identifier in identifiers if identifier.endswith('0'))
    assert len(calls) == 4
    assert [len(parameters) for _, parameters in calls[1:]] == [128, 128, 4]


def test_regrouped_lookup_empty_or_missing_optional_schema_needs_no_connection():
    from tempo_legacy_database.adapters.study.recombination import regrouped_presentation_ids
    calls = []

    def read(statement, parameters):
        calls.append((statement, parameters))
        return [(False,)]

    assert regrouped_presentation_ids(None, (), read=read) == frozenset()
    assert calls == []
    assert regrouped_presentation_ids(None, ('known-physical',), read=read) == frozenset()
    assert len(calls) == 1 and 'to_regclass' in calls[0][0]


# The separate #487 scoped-conflict extension remains on qualified R11 branch
# 04cd9535. Its unmerged conflict tables/commands are not part of this current
# integration base or the bounded #503 port. Current source/integrity fences
# remain required by CurrentStudyRecombination; no such assertion is removed.
