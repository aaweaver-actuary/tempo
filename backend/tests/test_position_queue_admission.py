
from app.repertoire import position_study_source as _source_preparation
from types import SimpleNamespace as NS

from tempo_evidence_math.position_learning import Hold
from app.services.position_queue_admission import select_carrier


def test_due_consumers_apply_position_ownership_only_when_schema_present(monkeypatch):
    import psycopg
    from tempo_storage.queue_refresh import eligibility
    from tempo_storage.queue_refresh.application import (
        admission,
        seeding,
    )

    class Connection:
        pass

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class Database:
        def __init__(self, present):
            self.raw = Connection()
            self.present = present
            self.statements = []

        def execute_native(self, statement, parameters=()):
            self.statements.append((statement, parameters))
            if "to_regclass" in statement:
                return Result([(self.present,)])
            return Result([])

        def execute(self, statement, parameters=()):
            self.statements.append((statement, parameters))
            return Result([])

    monkeypatch.setattr(psycopg, "Connection", Connection)
    for present in (False, True):
        database = Database(present)
        assert not admission.has_missing_due_card(database, "2026-11-02")
        assert seeding.due_cards(database, "2026-11-02") == []
        queries = [sql for sql, _ in database.statements if "FROM cards" in sql]
        assert len(queries) == 2
        assert all(("study_position_owners" in sql) == present for sql in queries)
        if present:
            assert eligibility._POSITION_DUE_EXCLUSION in queries[0]
            assert (
                eligibility._POSITION_DUE_EXCLUSION.replace("c.", "cards.")
                in queries[1]
            )
    assert eligibility.position_due_exclusion(NS(raw=object())) == ""


def test_due_owner_schema_probe_failure_is_not_treated_as_legacy(monkeypatch):
    import psycopg
    import pytest
    from tempo_storage.queue_refresh.eligibility import (
        position_due_exclusion,
    )

    class Connection:
        pass

    monkeypatch.setattr(psycopg, "Connection", Connection)

    def fail(*args):
        raise RuntimeError("owner schema unavailable")

    with pytest.raises(RuntimeError, match="owner schema unavailable"):
        position_due_exclusion(NS(raw=Connection(), execute_native=fail))


def binding(card, *, repertoire="r", response="e2e4", available=True):
    return NS(
        repertoire_id=repertoire,
        position_key="position",
        selected_response_uci=response,
        presentation_id=card,
        presentation_revision=1,
        decision_id=card,
        available=available,
        introduction="introduced",
        introduction_matches_current_content=True,
    )


def source(bindings):
    key = NS(repertoire_id="r", position="position")
    target = NS(key=key, expected_uci="e2e4")
    return NS(
        authored_token=NS(repertoire_id="r"),
        snapshot=NS(decision_presentations=bindings),
        routes=[NS(decisions=[NS(definition=target)])],
    ), target


def test_same_repertoire_aliases_choose_one_deterministic_carrier():
    prepared, target = source(
        [binding("b"), binding("a"), binding("x", repertoire="other")]
    )
    assert select_carrier(prepared, target).presentation_id == "a"
    assert select_carrier(prepared, target, card_id="b").presentation_id == "b"


def test_conflicting_response_or_unavailable_carrier_holds():
    prepared, target = source([binding("a", response="d2d4")])
    assert isinstance(select_carrier(prepared, target), Hold)
    prepared, target = source([binding("a", available=False)])
    assert isinstance(select_carrier(prepared, target), Hold)


def test_refresh_consumes_prepared_due_before_advancing(monkeypatch):
    from contextlib import contextmanager
    from app.services import postgres_queue_refresh as refresh
    from app.services import position_queue_admission as position
    from app.services import study_mode

    @contextmanager
    def borrowed(*args, **kwargs):
        yield object()

    calls = []
    prepared = {
        "cursor": {"position_repertoire": "r", "position_after": ["t", "standard", "p"]}
    }
    monkeypatch.setattr(refresh, "background_read_connection", borrowed)
    monkeypatch.setattr(refresh, "read_connection", borrowed)
    monkeypatch.setattr(refresh, "connection", borrowed)
    monkeypatch.setattr(
        refresh.frozen_opening_order, "activation", lambda *a, **k: (None, 0)
    )
    monkeypatch.setattr(refresh.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(study_mode, "study_only_enabled", lambda: False)
    monkeypatch.setattr(refresh, "lock_current_slice", lambda *a: True)
    monkeypatch.setattr(
        position, "prepare_due", lambda *a: calls.append("prepare") or prepared
    )
    monkeypatch.setattr(position, "admit_due", lambda *a: calls.append("admit"))
    monkeypatch.setattr(
        refresh,
        "advance_task_slice_in_transaction",
        lambda db, task, **kw: calls.append(kw) or True,
    )
    assert refresh.execute_postgres_queue_refresh_slice(
        {
            "payload": {
                "queue_date": "2026-11-02",
                "_queue_phase": "admit_position_due",
                "_refresh_cycle_epoch": 0,
            }
        }
    )
    assert calls[:2] == ["prepare", "admit"]
    assert calls[2]["next_phase"] == "admit_position_due"
    assert calls[2]["next_payload"]["position_repertoire"] == "r"


def test_admission_fences_before_card_and_date_and_deduplicates(monkeypatch):
    from datetime import date, datetime, timezone
    from app.services.position_queue_admission import admit_due
    from tempo_storage.authored_repertoires import (
        position_study_source,
    )
    from tempo_storage.study import (
        position_owner_epoch,
        position_due_candidates,
    )
    from tempo_storage.queue_refresh import admissions
    from tempo_storage import queue_position_lock

    order = []
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    prepared, target = source([binding("a")])
    prepared.authored_token.authored_digest = "digest"
    state = NS(definition=target, owner_epoch="epoch", version=1)
    candidate = NS(state=state, witness=NS(issued_at=now))
    proposal = {
        "source": prepared,
        "candidate": candidate,
        "carrier": prepared.snapshot.decision_presentations[0],
    }
    monkeypatch.setattr(
        position_study_source,
        "fence_position_source",
        lambda *a: order.append("source"),
    )
    monkeypatch.setattr(
        position_owner_epoch,
        "owner_mode",
        lambda *a: order.append("owner") or NS(disposition="position", epoch="epoch"),
    )

    class Reader:
        def __init__(self, *a, **kw):
            self.states = self

        def lock_current(self, *a):
            order.append("state")
            return state

        def read_context(self, *a):
            return (NS(due=True),)

    monkeypatch.setattr(position_due_candidates, "PositionDueCandidateReader", Reader)
    monkeypatch.setattr(
        admissions, "locked_card", lambda *a: order.append("card") or True
    )
    monkeypatch.setattr(
        queue_position_lock,
        "lock_queue_date_for_position",
        lambda *a: order.append("day"),
    )
    monkeypatch.setattr(
        admissions, "position_carrier_eligible", lambda *a: {"occupied": True}
    )
    monkeypatch.setattr(
        admissions, "append_position_due", lambda *a: order.append("append")
    )
    assert admit_due(object(), date(2026, 11, 2).isoformat(), proposal) is None
    assert order == ["source", "owner", "state", "card", "day"]


def test_native_refresh_admits_due_carrier_and_reuses_pending(monkeypatch):
    """Synthetic schema with real source, H1 and sliced queue admission."""
    import os
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from uuid import uuid4
    import pytest

    dsn = os.environ.get("TEMPO_POSITION_TEST_DSN")
    if not dsn:
        pytest.skip("requires task-owned TEMPO_POSITION_TEST_DSN")
    import psycopg
    from psycopg import sql
    from tempo_evidence_math.position_learning import PositionKey, PositionDefinition
    from tempo_storage.postgres import tempo_row_factory
    from tempo_storage.authored_repertoires import (
        position_study_source,
    )
    from app.services import postgres_queue_refresh as refresh, study_mode

    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
    definition = PositionDefinition(PositionKey("r", fen), "e2e4", "p1")
    import chess

    board = chess.Board()
    board.push_uci("e2e4")
    board.push_uci("e7e5")
    second_fen = " ".join(board.fen().split()[:4])
    second_definition = PositionDefinition(PositionKey("r", second_fen), "g1f3", "p1")
    schema = "position_h2_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True, row_factory=tempo_row_factory) as raw:
        assert (
            raw.execute("SELECT current_database()")
            .fetchone()[0]
            .startswith("tempo_position_test_")
        )
        raw.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            from test_product_snapshot_postgres import DDL
            import json

            raw.execute(DDL)
            raw.execute("ALTER TABLE cards ADD COLUMN fsrs_card_json text")
            raw.execute("""CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY);
                CREATE TABLE settings(id integer PRIMARY KEY, timezone text NOT NULL);
                INSERT INTO settings VALUES(1,'UTC');
                CREATE TABLE repertoire_integrity_state(repertoire_id text PRIMARY KEY,
                    status text,scan_status text,scan_generation text,checked_at text,
                    scan_completed_sources bigint,scan_total_sources bigint,scan_error text);
                CREATE TABLE background_tasks(id text,kind text,deduplication_key text,
                    generation bigint,state text,payload_json text);
                CREATE TABLE daily_queue(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                  queue_date text,card_id text,cycle bigint,position bigint,card_bucket text,
                  admission_kind text,admission_repertoire_id text,admission_source text,status text DEFAULT 'queued',
                  UNIQUE(queue_date,card_id,cycle));
                CREATE TABLE queue_attempt_origins(queue_entry_id bigint,card_id text,revision bigint,
                  queue_date text,cycle bigint,admission_repertoire_id text,issued_as_head int,
                  start_fen text,moves_json text,trained_color text,content_type text);
                CREATE TABLE daily_queue_days(queue_date text PRIMARY KEY);
                CREATE TABLE opening_evidence_attempts(attempt_id text,card_id text,card_revision bigint,
                  queue_entry_id bigint,repertoire_id text);
                CREATE TABLE review_attempt_receipts(attempt_id text)""")
            ddl = (
                Path(__file__).resolve().parents[2]
                / "database/tempo_storage/ddl/migrations/057_position_study.sql"
            )
            raw.execute(ddl.read_text())
            moves = json.dumps(["e2e4", "e7e5", "g1f3"])
            raw.execute("INSERT INTO legacy_learning_identity VALUES(1,'fixture')")
            raw.execute("INSERT INTO repertoires VALUES('r','Synthetic',7,0,'[]')")
            raw.execute("INSERT INTO opening_graph_publications VALUES('r',3,'ready')")
            raw.execute(
                "INSERT INTO repertoire_lines VALUES('route','r','Route','white',%s,%s)",
                (chess.STARTING_FEN, moves),
            )
            raw.execute(
                "INSERT INTO cards VALUES('card','r',1,%s,%s,'white','opening',0,0,NULL,1,%s,NULL)",
                (chess.STARTING_FEN, moves, now.isoformat()),
            )
            raw.execute("INSERT INTO repertoire_cards VALUES('r','card',1)")
            raw.execute(
                "INSERT INTO opening_graph_steps VALUES('r',3,'route',0,'card',%s,%s,'white')",
                (chess.STARTING_FEN, moves),
            )
            raw.execute(
                "INSERT INTO repertoire_integrity_state VALUES('r','clean','idle','scan:2','2026-10-26T12:00:00Z',1,1,NULL)"
            )
            raw.execute(
                "INSERT INTO background_tasks VALUES('graph','opening_graph_rebuild','r',3,'complete','{}'),('scan','integrity_scan','r',2,'complete',%s)",
                (json.dumps(dict(repertoire_id="r", graph_generation=3)),),
            )

            class Port:
                read_only = False
                in_transaction = True

                def __init__(self, raw):
                    self.raw = raw

                def execute(self, query, params=()):
                    return self.raw.execute(query, params)

                execute_native = execute

            port = Port(raw)
            prepared = _source_preparation.prepare_position_source(port, "r")
            assert not isinstance(prepared, Hold)
            digest = prepared.authored_token.authored_digest
            raw.execute(
                "INSERT INTO study_position_epochs VALUES('r','e1',%s,%s)",
                (digest, now),
            )
            raw.execute(
                "INSERT INTO study_position_owners VALUES('r','e1','active',%s)", (now,)
            )
            for internal_id, position, answer in [
                (1, fen, "e2e4"),
                (2, second_fen, "g1f3"),
            ]:
                raw.execute(
                    """INSERT INTO study_position_states
                    (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                     fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                    VALUES('r','standard',%s,%s,%s,'e1',%s,%s,'{}',%s,%s)""",
                    (
                        position,
                        answer,
                        next(
                            d.definition.policy_version
                            for r in prepared.routes
                            for d in r.decisions
                            if d.definition.key.position == position
                        ),
                        digest,
                        internal_id,
                        now - timedelta(days=1),
                        now - timedelta(days=1),
                    ),
                )

            @contextmanager
            def borrowed(*args, **kwargs):
                yield port

            monkeypatch.setattr(refresh, "background_read_connection", borrowed)
            monkeypatch.setattr(refresh, "read_connection", borrowed)
            monkeypatch.setattr(refresh, "connection", borrowed)
            monkeypatch.setattr(
                refresh.frozen_opening_order, "activation", lambda *a, **k: (None, 0)
            )
            monkeypatch.setattr(
                refresh.activity_gate, "wait_for_foreground", lambda: None
            )
            monkeypatch.setattr(study_mode, "study_only_enabled", lambda: False)
            monkeypatch.setattr(refresh, "lock_current_slice", lambda *a: True)
            saved = []
            monkeypatch.setattr(
                refresh,
                "advance_task_slice_in_transaction",
                lambda db, task, **kw: saved.append(kw) or True,
            )
            raw.execute("BEGIN")
            _, authoritative_day = refresh._queue_slice_current_day(port)
            assert authoritative_day == day
            assert refresh.execute_postgres_queue_refresh_slice(
                {"payload": {"queue_date": day, "_queue_phase": "admit_position_due",
                             "_refresh_cycle_epoch": 1}}
            )
            raw.execute("COMMIT")
            assert [
                tuple(r)
                for r in raw.execute(
                    "SELECT card_id,cycle,admission_repertoire_id FROM daily_queue"
                ).fetchall()
            ] == [("card", 0, "r")]
            entry = raw.execute("SELECT id FROM daily_queue").fetchone()[0]
            raw.execute("INSERT INTO daily_queue_days VALUES(%s)", (day,))
            raw.execute(
                """INSERT INTO queue_attempt_origins
                VALUES(%s,'card',1,%s,0,'r',0,%s,%s,'white','opening')""",
                (entry, day, chess.STARTING_FEN, moves),
            )
            # Current ordinary due admission owns selection. Removed Feedback
            # urgency/held-effect settlement is not a current issuance contract.
            from tempo_storage import connections
            from app.services import queue_next_card
            from app import queue_commands
            target_definition = next(
                d.definition for route in prepared.routes for d in route.decisions
                if d.definition.key.position == min(fen, second_fen)
            )

            monkeypatch.setattr(connections, "read_connection", borrowed)
            monkeypatch.setattr(queue_commands, "current_study_day", lambda db: day)
            monkeypatch.setattr(queue_commands, "_lock_attempt_card", lambda *a: None)
            monkeypatch.setattr(
                queue_commands.queue_storage, "issued_origin", lambda *a: True
            )
            monkeypatch.setattr(
                queue_commands.queue_storage,
                "prepare_queue_head",
                lambda *a: lambda d: {"id": entry},
            )
            def validate_available_attempt(database, entry_id, card_id, revision, *, current_day):
                assert database is port
                assert (entry_id, card_id, revision) == (entry, "card", 1)
                assert current_day == day

            monkeypatch.setattr(
                queue_commands, "validate_available_attempt", validate_available_attempt
            )
            monkeypatch.setattr(
                queue_commands.queue_storage,
                "stamp_issued_head",
                lambda db, *a: (
                    raw.execute(
                        "UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s",
                        (entry,),
                    ).rowcount
                ),
            )
            envelope = {
                "queue_date": day,
                "entry_id": entry,
                "card_id": "card",
                "expected_revision": 1,
            }
            raw.execute("BEGIN")
            proposal = queue_next_card.prepare_queue_head(envelope)
            assert proposal.mode == "position", proposal.reason
            assert proposal.proposal.plan.target == target_definition
            assert proposal.proposal.plan.queue_entry_id == entry
            assert proposal.proposal.plan.original_card_id == "card"
            assert proposal.proposal.plan.original_card_revision == 1
            assert proposal.proposal.plan.local_day.isoformat() == day
            assert (
                next(
                    w
                    for w in proposal.proposal.plan.due_positions
                    if w.definition == target_definition
                ).due
                is True
            )
            # Preparation has not issued an attempt or written an issuance plan.
            raw.execute("ROLLBACK")
            assert raw.execute("SELECT count(*) FROM study_position_issued_plans").fetchone()[0] == 0
            raw.execute("BEGIN")
            raw.execute(
                "UPDATE repertoire_lines SET moves_json='[\"d2d4\"]' WHERE id='route'"
            )
            held = queue_commands.issue_queue_head(port, proposal)
            assert held["position_study_hold"]
            raw.execute("ROLLBACK")
            assert (
                raw.execute(
                    "SELECT count(*) FROM study_position_issued_plans"
                ).fetchone()[0]
                == 0
            )
            raw.execute("BEGIN")
            monkeypatch.setattr(
                queue_commands.queue_storage,
                "prepare_queue_head",
                lambda *a: lambda d: {"id": entry + 100},
            )
            from app.contracts.review_conflicts import ReviewConflict
            from app.study.position_learning import issuance

            original_issue = issuance.issue_in_borrowed_transaction
            witnessed = []
            def witness_real_plan(database, prepared):
                result = original_issue(database, prepared)
                plan = prepared.plan
                row = raw.execute(
                    "SELECT attempt_id,queue_entry_id,card_id,card_revision,plan_digest,plan_json "
                    "FROM study_position_issued_plans WHERE attempt_id=%s", (plan.attempt_id,)
                ).fetchone()
                assert row is not None
                assert tuple(row) == (plan.attempt_id, entry, "card", 1, plan.plan_id, plan.to_record())
                witnessed.append(plan.attempt_id)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(issuance, "issue_in_borrowed_transaction", witness_real_plan)
                with pytest.raises(ReviewConflict) as failure:
                    queue_commands.issue_queue_head(port, proposal)
            assert failure.value.code == "position_head_changed"
            assert failure.value.retryable is True
            assert witnessed == [proposal.proposal.plan.attempt_id]
            raw.execute("ROLLBACK")
            assert raw.execute(
                "SELECT count(*) FROM study_position_issued_plans WHERE attempt_id=%s",
                (proposal.proposal.plan.attempt_id,),
            ).fetchone()[0] == 0
            monkeypatch.setattr(
                queue_commands.queue_storage,
                "prepare_queue_head",
                lambda *a: lambda d: {"id": entry},
            )
            raw.execute("BEGIN")
            result = queue_commands.issue_queue_head(port, proposal)
            assert result["issued"] and result["position_study_hold"] is None
            raw.execute("COMMIT")
            first_wire = result["position_presentation"]
            replay = queue_next_card.prepare_queue_head(envelope)
            assert replay.mode == "replay" and replay.proposal.to_wire() == first_wire
            cursor_payload = saved[-1]["next_payload"]
            raw.execute("BEGIN")
            assert refresh.execute_postgres_queue_refresh_slice(
                {"payload": cursor_payload}
            )
            raw.execute("COMMIT")
            assert raw.execute("SELECT count(*) FROM daily_queue").fetchone()[0] == 1
            raw.execute("UPDATE daily_queue SET status='complete'")
            raw.execute(
                "UPDATE study_position_states SET due_at=%s WHERE repertoire_id='r'",
                (now - timedelta(days=1),),
            )
            raw.execute("BEGIN")
            assert refresh.execute_postgres_queue_refresh_slice(
                {"payload": {"queue_date": day, "_queue_phase": "admit_position_due",
                             "_refresh_cycle_epoch": 1}}
            )
            raw.execute("COMMIT")
            assert [
                r[0]
                for r in raw.execute(
                    "SELECT cycle FROM daily_queue ORDER BY cycle"
                ).fetchall()
            ] == [0, 1]
        finally:
            raw.execute("ROLLBACK")
            raw.execute("SET search_path TO public")
            raw.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_issued_head_replays_exact_attempt_without_rebuilding_source(monkeypatch):
    from contextlib import contextmanager
    from app.services.queue_next_card import prepare_queue_head
    from tempo_storage import connections
    from app.study.position_learning import issuance as position_issued_plans

    calls = []
    plan = NS(to_wire=lambda: {"attempt_id": "original"})

    class Cursor:
        def fetchall(self):
            return [{"attempt_id": "original"}]

    class Reader:
        def execute_native(self, sql, params):
            calls.append((sql, params))
            return Cursor()

    @contextmanager
    def borrowed():
        yield Reader()

    monkeypatch.setattr(connections, "read_connection", borrowed)
    monkeypatch.setattr(
        position_issued_plans, "replay_exact_attempt", lambda db, attempt: plan
    )
    result = prepare_queue_head(
        {
            "queue_date": "2026-11-02",
            "entry_id": 4,
            "card_id": "card",
            "expected_revision": 2,
        }
    )
    assert result.mode == "replay" and result.proposal is plan
    assert len(calls) == 1 and calls[0][1] == (4, "card", 2)


def test_new_position_issue_fences_source_before_existing_queue_locks_and_rolls_back_stale_head(
    monkeypatch,
):
    import pytest
    from fastapi import HTTPException
    from app import queue_commands
    from app.services import queue_next_card
    from tempo_storage.authored_repertoires import (
        position_study_source,
    )
    from app.study.position_learning import issuance as position_issued_plans

    calls = []
    payload = {
        "queue_date": "2026-11-02",
        "entry_id": 4,
        "card_id": "card",
        "expected_revision": 2,
    }
    proposal = NS(source=object())
    prepared = queue_next_card.PreparedQueueHead(payload, "position", proposal)
    monkeypatch.setattr(
        queue_commands, "current_study_day", lambda db: payload["queue_date"]
    )
    monkeypatch.setattr(
        position_study_source,
        "fence_position_source",
        lambda *a: calls.append("source"),
    )
    monkeypatch.setattr(
        position_issued_plans,
        "issue_in_borrowed_transaction",
        lambda *a: calls.append("plan") or object(),
    )
    monkeypatch.setattr(
        queue_commands, "_lock_attempt_card", lambda *a: calls.append("card")
    )
    monkeypatch.setattr(
        queue_commands, "lock_queue_date_for_position", lambda *a: calls.append("date")
    )
    monkeypatch.setattr(queue_commands.queue_storage, "retired_issuance_target", lambda *a: False)
    monkeypatch.setattr(queue_commands.queue_storage, "issued_origin", lambda *a: True)
    monkeypatch.setattr(
        queue_commands.queue_storage,
        "prepare_queue_head",
        lambda *a: lambda day: {"id": 99},
    )
    with pytest.raises(HTTPException) as failure:
        queue_commands.issue_queue_head(object(), prepared)
    assert failure.value.status_code == 409
    assert failure.value.code == "position_head_changed"
    assert failure.value.retryable is True
    assert calls == ["source", "plan", "card", "date"]


def test_source_preparation_cache_invalidates_on_source_revision(monkeypatch):
    from app.services import queue_next_card
    from tempo_storage.http_composition import queue_projection
    from tempo_storage.authored_repertoires import (
        position_study_source,
    )

    queue_next_card._source_cache.clear()
    revision, calls = [1], []
    monkeypatch.setattr(
        queue_projection,
        "position_source_revision",
        lambda *a: (revision[0], 2, 3, "ready"),
    )
    monkeypatch.setattr(
        _source_preparation,
        "prepare_position_source",
        lambda *a: calls.append(1) or NS(),
    )
    first = queue_next_card._cached_position_source(object(), "rep", "e1", "digest")
    assert (
        queue_next_card._cached_position_source(object(), "rep", "e1", "digest")
        is first
    )
    revision[0] += 1
    assert (
        queue_next_card._cached_position_source(object(), "rep", "e1", "digest")
        is not first
    )
    assert len(calls) == 2
    queue_next_card._source_cache.clear()


def test_http_issued_head_returns_original_plan_without_dispatch(monkeypatch):
    from contextlib import contextmanager
    from app import main
    from app.services import queue_next_card

    plan = NS(to_wire=lambda: {"attempt_id": "saved", "plan_id": "same"})

    @contextmanager
    def borrowed():
        yield object()

    monkeypatch.setattr(main, "read_connection", borrowed)
    monkeypatch.setattr(
        main.queue_projection_storage, "issued_head", lambda *a: {"issued_as_head": 1}
    )

    monkeypatch.setattr(
        queue_next_card,
        "prepare_queue_head",
        lambda payload: queue_next_card.PreparedQueueHead(payload, "replay", plan),
    )
    result = main._issue_returned_queue_head(
        "2026-11-02", {"queue_entry_id": 4, "id": "card", "revision": 2}
    )
    assert result == {
        "issued": True,
        "position_study_mode": "position",
        "position_presentation": {"attempt_id": "saved", "plan_id": "same"},
        "position_study_hold": None,
    }


def test_changed_source_holds_before_position_plan_or_queue_locks(monkeypatch):
    # SPEC November 10 pass 2 retires active Games urgency; current Study
    # source fencing still protects an issued position proposal.
    from app import queue_commands
    from app.services import queue_next_card
    from tempo_storage.authored_repertoires import position_study_source
    from app.study.position_learning import issuance

    payload = {"queue_date": "2026-11-02", "entry_id": 4,
               "card_id": "card", "expected_revision": 2}
    prepared = queue_next_card.PreparedQueueHead(payload, "position", NS(source=object()))
    calls = []
    monkeypatch.setattr(queue_commands, "current_study_day", lambda db: payload["queue_date"])
    monkeypatch.setattr(position_study_source, "fence_position_source",
                        lambda *a: calls.append("source") or Hold("position_owner_source_changed"))
    monkeypatch.setattr(issuance, "issue_in_borrowed_transaction", lambda *a: calls.append("plan"))
    monkeypatch.setattr(queue_commands, "_lock_attempt_card", lambda *a: calls.append("card"))
    monkeypatch.setattr(queue_commands, "lock_queue_date_for_position", lambda *a: calls.append("date"))
    result = queue_commands.issue_queue_head(object(), prepared)
    assert result["issued"] is False
    assert result["position_study_hold"] == "position_owner_source_changed"
    assert calls == ["source"]


def test_owner_read_contract_preserves_exact_selection_and_routing(monkeypatch):
    from dataclasses import FrozenInstanceError
    import pytest
    from app.services import position_queue_admission as admission, queue_next_card
    from tempo_storage.study import position_owner_epoch as storage

    class Database:
        def __init__(self):
            self.rows = []
            self.calls = []
        def execute_native(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return NS(fetchone=lambda: self.rows.pop(0))

    db = Database()
    db.rows = [dict(repertoire_id="r", epoch="e", source_digest="digest"),
               dict(repertoire_id="r", epoch="e", source_digest="digest"),
               dict(mode="shadow", epoch="e", source_digest="digest")]
    first = storage.next_active_owner(db, "q")
    assert first == storage.PositionOwnerFacts("r", "active", "e", "digest")
    with pytest.raises(FrozenInstanceError):
        first.epoch = "other"
    assert storage.active_owner(db, "r") == first
    assert storage.current_owner(db, "r").mode == "shadow"
    assert "o.mode='active' AND o.repertoire_id>%s ORDER BY o.repertoire_id LIMIT 1" in db.calls[0][0]
    assert "o.mode='active' AND o.repertoire_id=%s" in db.calls[1][0]
    assert "WHERE o.repertoire_id=%s" in db.calls[2][0]
    assert [params for _, params in db.calls] == [("q",), ("r",), ("r",)]

    # An independent fake returns the same immutable semantic values, without SQL.
    monkeypatch.setattr(storage, "next_active_owner", lambda *_: None)
    monkeypatch.setattr(storage, "active_owner", lambda *_: None)
    assert admission.prepare_due(object(), "2026-11-02", {}) is None
    assert admission.prepare_due(object(), "2026-11-02", {"position_repertoire": "r"}).reason == "position_owner_changed_during_admission"
    monkeypatch.setattr(storage, "active_owner", lambda *_: first)
    for epoch, digest in (("other", "digest"), ("e", "other")):
        assert admission.prepare_due(object(), "2026-11-02", {"position_repertoire": "r", "position_epoch": epoch, "position_digest": digest}).reason == "position_owner_changed_during_admission"
    for mode in (None, "inactive", "shadow", "staged", "paused"):
        value = None if mode is None else storage.PositionOwnerFacts("r", mode, "e", "digest")
        monkeypatch.setattr(storage, "current_owner", lambda *_, value=value: value)
        result = admission.prepare_opening_introduction(object(), {"repertoire_id": "r", "card_id": "card"})
        if mode in (None, "inactive", "shadow"):
            assert result == ("legacy", "r")
        else:
            assert result.reason == "position_owner_" + mode
    monkeypatch.setattr(storage, "current_owner", lambda *_: first)
    monkeypatch.setattr(queue_next_card, "_cached_position_source", lambda *_: NS(authored_token=NS(authored_digest="changed")))
    assert admission.prepare_opening_introduction(object(), {"repertoire_id": "r", "card_id": "card"}).reason == "position_owner_source_changed"


def test_due_preparation_consumes_semantic_owner_facts_and_retains_cursor(monkeypatch):
    from datetime import datetime, timezone
    from app.services import position_queue_admission as admission
    from tempo_storage.study import position_due_candidates, position_owner_epoch

    owner = position_owner_epoch.PositionOwnerFacts("r", "active", "epoch", "digest")
    instant = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    prepared_source, target = source([binding("card")])
    prepared_source.authored_token.authored_digest = "digest"
    state = NS(definition=target, due_at=instant)
    state.definition.key.variant = "standard"
    candidate = NS(state=state)
    seen = []
    class Reader:
        def __init__(self, database, **kwargs):
            seen.append((database, kwargs))
        def read_due_page(self, *, after, limit):
            assert after is None and limit == 1
            return NS(candidates=[candidate])
    monkeypatch.setattr(position_owner_epoch, "next_active_owner", lambda db, after: owner)
    monkeypatch.setattr(position_due_candidates, "PositionDueCandidateReader", Reader)
    monkeypatch.setattr(_source_preparation, "prepare_position_source", lambda db, rep: prepared_source)
    db = object()
    result = admission.prepare_due(db, "2026-11-02", {"position_instant": instant.isoformat()})
    assert result["source"] is prepared_source and result["candidate"] is candidate
    assert result["carrier"].presentation_id == "card"
    assert result["cursor"] == {"position_repertoire": "r", "position_epoch": "epoch", "position_digest": "digest",
        "position_instant": instant.isoformat(), "position_after": [instant.isoformat(), "standard", "position"]}
    assert seen[0][0] is db and seen[0][1]["owner_epoch"] == "epoch" and seen[0][1]["source_digest"] == "digest"
