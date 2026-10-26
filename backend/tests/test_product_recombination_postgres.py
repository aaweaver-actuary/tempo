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
