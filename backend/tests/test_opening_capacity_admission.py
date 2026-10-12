"""Capacity policy meets the existing writers; these are not PostgreSQL durability proofs."""

from datetime import date
import json

import chess
import pytest

from app import database, main
from app.services.opening_graph import GraphInput, build_graph


@pytest.fixture
def capacity_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "capacity.db")
    database.initialize()
    day = date.today().isoformat()
    with database.connection() as connection:
        connection.execute(
            "UPDATE settings SET new_cards_per_day=10,tactics_new_per_day=0,study_new_per_day=0 WHERE id=1"
        )
        connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Capacity','fixture',?)",
            (day,),
        )
        lines = []
        for prefix_move in ("e2e4", "d2d4"):
            prefix_board = chess.Board()
            prefix_board.push_uci(prefix_move)
            for reply_index, opponent_move in enumerate(
                sorted(prefix_board.legal_moves, key=lambda move: move.uci())[:4]
            ):
                board = prefix_board.copy()
                board.push(opponent_move)
                moves = [prefix_move, opponent_move.uci()]
                for _ in range(3):
                    next_move = sorted(board.legal_moves, key=lambda move: move.uci())[
                        0
                    ]
                    moves.append(next_move.uci())
                    board.push(next_move)
                line_id = f"{prefix_move}-{reply_index}"
                line = {
                    "id": line_id,
                    "start_fen": chess.STARTING_FEN,
                    "moves_json": json.dumps(moves),
                    "trained_color": "white",
                }
                lines.append(line)
                connection.execute(
                    "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,'rep',?,'white',?,?,?)",
                    (line_id, line_id, line["start_fen"], line["moves_json"], day),
                )
        steps = build_graph(GraphInput("rep", tuple(lines), 1))
        for step in steps:
            connection.execute(
                "INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES(?,'rep',?,?,?,'locked',?,'opening')",
                (
                    step.card_id,
                    "prefix" if step.segment_kind == "prefix" else "response",
                    step.starting_fen,
                    json.dumps(step.moves),
                    day,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)",
                (step.card_id,),
            )
            connection.execute(
                "INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES('rep',1,?,?,?,?,?,?,?,'white',?,?,?,?)",
                (
                    step.line_id,
                    step.decision_index,
                    step.card_id,
                    step.parent_card_id,
                    step.decision_fen_key,
                    step.starting_fen,
                    json.dumps(step.moves),
                    step.segment_kind,
                    step.first_decision_index,
                    step.last_decision_index,
                    json.dumps(step.decision_fen_keys),
                ),
            )
        connection.execute(
            "INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('rep',1,'ready',?)",
            (day,),
        )
        main._unlock_eligible_opening_cards(connection, day)
        yield connection, day, steps


def test_capacity_two_prefixes_fill_ten_without_maturity_or_recursive_unlock(
    capacity_database, monkeypatch
):
    connection, day, steps = capacity_database
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    main.admit_prioritized_opening_cards(connection, day, {"rep": 10}, -1)
    expected = {step.card_id for step in steps if step.decision_index <= 1}
    assert len(expected) == 10
    assert {
        row[0] for row in connection.execute("SELECT card_id FROM daily_queue")
    } == expected
    assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM cards WHERE state='mature'"
        ).fetchone()[0]
        == 0
    )
    for _ in range(3):
        main._unlock_eligible_opening_cards(connection, day)
        main.admit_prioritized_opening_cards(connection, day, {"rep": 10}, 9)
    assert {
        row[0] for row in connection.execute("SELECT card_id FROM daily_queue")
    } == expected
    assert all(
        connection.execute(
            "SELECT state FROM cards WHERE id=?", (step.card_id,)
        ).fetchone()[0]
        == "locked"
        for step in steps
        if step.decision_index == 2
    )


class NativeSqlite:
    """Portable worker SQL adapter; deliberately does not emulate database locks."""

    def __init__(self, connection):
        self.connection = connection
        self.database = connection

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace("FOR UPDATE", ""), parameters)

    def execute_native(self, statement, parameters=()):
        return self.execute(statement.replace("%s", "?"), parameters)


def capacity_plan(connection, day):
    from app.services.opening_capacity_admission import (
        read_capacity_routes,
        capacity_priority_statement,
        propose_capacity_expansion_from_priority_rows,
        capacity_proposal_payload,
    )

    routes = read_capacity_routes(
        lambda statement, parameters: connection.execute(
            statement, parameters
        ).fetchall(),
        "rep",
        (),
    )
    priorities = connection.execute(
        f"WITH active_miss AS ({main.outstanding_miss_query(connection)}) "
        + capacity_priority_statement(main._PRIORITY_OPENING_CANDIDATE_BODY),
        (main.MISS_REASON, day, day, day, day),
    ).fetchall()
    return [
        capacity_proposal_payload(proposal)
        for proposal in propose_capacity_expansion_from_priority_rows(
            day,
            (),
            {"rep": 8},
            routes,
            priorities,
            miss_reason=main.MISS_REASON,
        )
    ]


def introduce_prefixes(connection, day, monkeypatch):
    monkeypatch.delenv("OPENING_CAPACITY_FRONTIER_EXPANSION", raising=False)
    main.admit_prioritized_opening_cards(connection, day, {"rep": 2}, -1)
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")


def test_capacity_disabled_matches_existing_two_prefix_admissions(
    capacity_database, monkeypatch
):
    connection, day, steps = capacity_database
    monkeypatch.delenv("OPENING_CAPACITY_FRONTIER_EXPANSION", raising=False)
    main.admit_prioritized_opening_cards(connection, day, {"rep": 10}, -1)
    assert {
        row[0] for row in connection.execute("SELECT card_id FROM daily_queue")
    } == {step.card_id for step in steps if step.decision_index == 0}


@pytest.mark.parametrize(
    "rejection",
    [
        "flag_disabled",
        "study_only",
        "generation",
        "publication_failed",
        "missing_line",
        "candidate_archived",
        "candidate_pending",
        "candidate_membership",
        "candidate_integrity",
        "parent_archived",
        "parent_pending",
        "parent_membership",
        "parent_integrity",
        "parent_not_introduced",
        "due_date",
        "deleted_card",
        "route_geometry",
    ],
)
def test_capacity_writer_rejects_changed_evidence_before_admission(
    capacity_database, monkeypatch, rejection
):
    from app.services import postgres_queue_refresh as worker

    connection, day, _ = capacity_database
    introduce_prefixes(connection, day, monkeypatch)
    planned = capacity_plan(connection, day)[0]
    child = planned["card_id"]
    parent = planned["capacity_route"]["parent_card_id"]
    if rejection == "flag_disabled":
        monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "0")
    elif rejection == "study_only":
        connection.execute("UPDATE settings SET study_only_mode=1 WHERE id=1")
    elif rejection == "generation":
        connection.execute("UPDATE opening_graph_publications SET generation=2")
    elif rejection == "publication_failed":
        connection.execute("UPDATE opening_graph_publications SET state='failed'")
    elif rejection == "missing_line":
        connection.execute(
            "DELETE FROM repertoire_lines WHERE id=?",
            (planned["capacity_route"]["line_id"],),
        )
    elif rejection in (
        "candidate_archived",
        "parent_archived",
        "candidate_pending",
        "parent_pending",
    ):
        column = "archived" if rejection.endswith("archived") else "pending_validation"
        connection.execute(
            f"UPDATE cards SET {column}=1 WHERE id=?",
            (parent if rejection.startswith("parent") else child,),
        )
    elif rejection in ("candidate_membership", "parent_membership"):
        identifier = parent if rejection.startswith("parent") else child
        connection.execute(
            "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES('__retained_cards__','Retained','fixture',?)",
            (day,),
        )
        connection.execute(
            "UPDATE cards SET repertoire_id='__retained_cards__' WHERE id=?",
            (identifier,),
        )
        connection.execute(
            "DELETE FROM repertoire_cards WHERE card_id=?", (identifier,)
        )
    elif rejection in ("candidate_integrity", "parent_integrity"):
        identifier = parent if rejection.startswith("parent") else child
        connection.execute(
            "INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) VALUES('block','rep','missing_response','fixture',?,?)",
            (day, day),
        )
        connection.execute(
            "INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at) VALUES('rep',?,'block','current',?)",
            (identifier, day),
        )
    elif rejection == "parent_not_introduced":
        connection.execute("UPDATE cards SET introduced_at=NULL WHERE id=?", (parent,))
    elif rejection == "due_date":
        connection.execute(
            "UPDATE cards SET due_date='2099-01-01' WHERE id=?", (child,)
        )
    elif rejection == "deleted_card":
        connection.execute(
            "INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?)", (child, day)
        )
    elif rejection == "route_geometry":
        connection.execute(
            "UPDATE opening_graph_steps SET first_decision_index=99 WHERE line_id=? AND decision_index=1",
            (planned["capacity_route"]["line_id"],),
        )
    monkeypatch.setattr(worker, "lock_queue_date_for_position", lambda *_: None)
    worker._admit_one_prioritized_opening(NativeSqlite(connection), day, planned)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM daily_queue WHERE card_id=?", (child,)
        ).fetchone()[0]
        == 0
    )
    assert connection.execute(
        "SELECT introduced_at,state FROM cards WHERE id=?", (child,)
    ).fetchone()[:] == (None, "locked")


def test_capacity_worker_parent_first_quota_recheck_and_interleaved_replay(
    capacity_database, monkeypatch
):
    from app.services import postgres_queue_refresh as worker

    connection, day, steps = capacity_database
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    monkeypatch.setattr(worker, "lock_queue_date_for_position", lambda *_: None)
    from app.services.opening_capacity_admission import (
        read_capacity_routes,
        capacity_proposal_payload,
        propose_capacity_expansion_from_priority_rows,
        capacity_priority_statement,
    )

    roots = {step.card_id for step in steps if step.decision_index == 0}
    normal = [("rep", {"id": root}) for root in sorted(roots)]
    routes = read_capacity_routes(
        lambda sql, parameters: connection.execute(sql, parameters).fetchall(),
        "rep",
        tuple(sorted(roots)),
    )
    rows = connection.execute(
        f"WITH active_miss AS ({main.outstanding_miss_query(connection)}) "
        + capacity_priority_statement(main._PRIORITY_OPENING_CANDIDATE_BODY),
        (main.MISS_REASON, day, day, day, day),
    ).fetchall()
    planned = [
        capacity_proposal_payload(proposal)
        for proposal in propose_capacity_expansion_from_priority_rows(
            day, normal, {"rep": 8}, routes, rows, miss_reason=main.MISS_REASON
        )
    ]
    worker._admit_one_prioritized_opening(NativeSqlite(connection), day, planned[0])
    assert connection.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 0
    introduce_prefixes(connection, day, monkeypatch)
    connection.execute("UPDATE repertoires SET new_cards_per_day=3 WHERE id='rep'")
    for item in (planned[0], planned[0], planned[1], planned[0]):
        worker._admit_one_prioritized_opening(NativeSqlite(connection), day, item)
    assert connection.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 3
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM daily_queue WHERE card_id=?", (planned[0]["card_id"],)
        ).fetchone()[0]
        == 1
    )


def test_capacity_writer_rechecks_invalidated_earlier_review_on_exact_route(
    capacity_database, monkeypatch
):
    from app.services import postgres_queue_refresh as worker

    connection, day, steps = capacity_database
    introduce_prefixes(connection, day, monkeypatch)
    child_plan = capacity_plan(connection, day)
    monkeypatch.setattr(worker, "lock_queue_date_for_position", lambda *_: None)
    native = NativeSqlite(connection)
    worker._admit_one_prioritized_opening(native, day, child_plan[0])
    parent = child_plan[0]["capacity_route"]["parent_card_id"]
    connection.execute(
        "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(?,'again',?,0,1,'study')",
        (parent, day),
    )
    deeper = [
        proposal
        for proposal in capacity_plan(connection, day)
        if proposal["capacity_route"]["decision_index"] == 2
    ]
    assert deeper
    connection.execute(
        "UPDATE reviews SET invalidated_at=? WHERE card_id=?", (day, parent)
    )
    for proposal in deeper:
        worker._admit_one_prioritized_opening(native, day, proposal)
    assert connection.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 3


def test_capacity_admissions_leave_fsrs_due_dates_and_reviews_unchanged(
    capacity_database, monkeypatch
):
    connection, day, _ = capacity_database
    before = connection.execute(
        "SELECT id,fsrs_card_json,interval_days,stability,due_date FROM cards ORDER BY id"
    ).fetchall()
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    main.admit_prioritized_opening_cards(connection, day, {"rep": 10}, -1)
    assert (
        connection.execute(
            "SELECT id,fsrs_card_json,interval_days,stability,due_date FROM cards ORDER BY id"
        ).fetchall()
        == before
    )
    assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_capacity_repeated_refresh_with_spare_allowance_and_day_rollover_stays_bounded(
    capacity_database, monkeypatch
):
    connection, day, steps = capacity_database
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    connection.execute("UPDATE settings SET new_cards_per_day=100 WHERE id=1")
    main.admit_prioritized_opening_cards(connection, day, {"rep": 100}, -1)
    for refresh_day in (day, day, "2026-10-13"):
        main._unlock_eligible_opening_cards(connection, refresh_day)
        main.admit_prioritized_opening_cards(connection, refresh_day, {"rep": 100}, 9)
    assert connection.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 10
    assert all(
        connection.execute(
            "SELECT state FROM cards WHERE id=?", (step.card_id,)
        ).fetchone()[0]
        == "locked"
        for step in steps
        if step.decision_index == 2
    )


def test_capacity_worker_preparation_preserves_normal_plan_and_returns_parent_dependencies(
    capacity_database, monkeypatch
):
    from app.services import postgres_queue_refresh as worker, study_mode

    connection, day, steps = capacity_database
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    monkeypatch.setattr(study_mode, "optional_work_paused", lambda database=None: False)
    pages = []

    def read(statement, parameters=(), *, native=False):
        if statement.startswith("WITH active_miss"):
            pages.append(parameters[1])
            sql = main._PRIORITY_OPENING_CANDIDATE_BODY
            if "AND (c.state='new'" not in statement:
                from app.services.opening_capacity_admission import (
                    capacity_priority_statement,
                )

                sql = capacity_priority_statement(sql)
            return connection.execute(
                f"WITH active_miss AS ({main.outstanding_miss_query(connection)}) "
                + sql
                + " AND c.id IN ("
                + ",".join("?" for _ in parameters[1])
                + ")",
                (main.MISS_REASON, day, day, day, day, *parameters[1]),
            ).fetchall()
        return connection.execute(statement.replace("%s", "?"), parameters).fetchall()

    monkeypatch.setattr(worker, "_bounded_read", read)
    planned = worker._prepare_prioritized_openings(day)
    assert len(planned) == 10
    assert all("capacity_route" not in item for item in planned[:2])
    assert all("capacity_route" in item for item in planned[2:])
    assert all(
        item["capacity_route"]["parent_card_id"]
        in {root["card_id"] for root in planned[:2]}
        for item in planned[2:]
    )
    assert all(len(page) <= worker._OPENING_CANDIDATE_READ_BATCH_SIZE for page in pages)


def test_capacity_disabled_or_full_allowance_performs_no_frontier_reads(
    capacity_database, monkeypatch
):
    from app.services import opening_capacity_admission as capacity

    connection, day, _ = capacity_database
    monkeypatch.setattr(
        capacity,
        "read_capacity_routes",
        lambda *_: pytest.fail("Unexpected frontier read"),
    )
    monkeypatch.delenv("OPENING_CAPACITY_FRONTIER_EXPANSION", raising=False)
    main.admit_prioritized_opening_cards(connection, day, {"rep": 2}, -1)
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    main.admit_prioritized_opening_cards(connection, day, {"rep": 2}, 1)


def test_capacity_study_only_suppresses_frontier_reads(capacity_database, monkeypatch):
    from app.services import opening_capacity_admission as capacity

    connection, day, _ = capacity_database
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    connection.execute("UPDATE settings SET study_only_mode=1 WHERE id=1")
    monkeypatch.setattr(
        capacity,
        "read_capacity_routes",
        lambda *_: pytest.fail("Study-only frontier read"),
    )
    main.admit_prioritized_opening_cards(connection, day, {"rep": 10}, -1)
    assert connection.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 2


def test_capacity_frontier_reads_only_bounded_direct_parent_pages_with_large_locked_backlog(
    capacity_database, monkeypatch
):
    from app.services.opening_capacity_admission import (
        read_capacity_routes,
        CAPACITY_READ_BATCH_SIZE,
    )

    connection, day, steps = capacity_database
    introduce_prefixes(connection, day, monkeypatch)
    connection.executemany(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES(?,'rep','response',?,'[\"e2e4\"]','locked',?,'opening')",
        ((f"unrelated-{index:05}", chess.STARTING_FEN, day) for index in range(15000)),
    )
    recorded = []

    def read(statement, parameters):
        rows = connection.execute(statement, parameters).fetchall()
        recorded.append((statement, parameters, len(rows)))
        if "FROM cards parent\n" in statement:
            assert "parent.id IN (" in statement
        return rows

    routes = read_capacity_routes(read, "rep", ())
    assert len(routes) == 8
    assert max(count for _, _, count in recorded) <= CAPACITY_READ_BATCH_SIZE
    assert len(recorded) == 5
    assert all(not card_id.startswith("unrelated-") for card_id, _ in routes)


def test_capacity_shared_card_is_charged_once_to_qualifying_repertoire(
    capacity_database, monkeypatch
):
    connection, day, steps = capacity_database
    connection.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES('other','Shared','fixture',?,10)",
        (day,),
    )
    connection.execute(
        "INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('other',1,'ready',?)",
        (day,),
    )
    for (line_id,) in connection.execute(
        "SELECT id FROM repertoire_lines WHERE repertoire_id='rep'"
    ).fetchall():
        connection.execute(
            "INSERT INTO repertoire_lines SELECT 'other-'||id,'other',name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines WHERE id=?",
            (line_id,),
        )
    connection.execute(
        "INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) SELECT 'other',generation,'other-'||line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json FROM opening_graph_steps WHERE repertoire_id='rep'"
    )
    connection.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id) SELECT 'other',id FROM cards WHERE repertoire_id='rep'"
    )
    monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "1")
    main.admit_prioritized_opening_cards(connection, day, {"rep": 0, "other": 10}, -1)
    main.admit_prioritized_opening_cards(connection, day, {"rep": 0, "other": 10}, 9)
    assert connection.execute(
        "SELECT COUNT(*),COUNT(DISTINCT card_id) FROM daily_queue"
    ).fetchone()[:] == (10, 10)
    assert {
        row[0]
        for row in connection.execute("SELECT admission_repertoire_id FROM daily_queue")
    } == {"other"}


def test_capacity_checkpoint_resume_retains_witness_and_rejects_old_lease(
    capacity_database, monkeypatch
):
    from contextlib import contextmanager
    from app.services import postgres_queue_refresh as worker, study_mode

    connection, day, _ = capacity_database
    introduce_prefixes(connection, day, monkeypatch)
    planned = capacity_plan(connection, day)
    monkeypatch.setattr(study_mode, "optional_work_paused", lambda database=None: False)
    monkeypatch.setattr(worker.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(worker.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(worker, "lock_queue_date_for_position", lambda *_: None)

    @contextmanager
    def section(*, background):
        assert background
        yield NativeSqlite(connection)

    monkeypatch.setattr(worker, "connection", section)
    current_lease = "first"
    saved = {}

    def advance(database, task, *, next_phase, next_payload):
        nonlocal current_lease
        saved.update(next_payload)
        current_lease = "second"
        return True

    monkeypatch.setattr(worker, "advance_task_slice_in_transaction", advance)
    monkeypatch.setattr(
        worker,
        "lock_current_slice",
        lambda database, task: task["lease_token"] == current_lease,
    )
    task = {
        "id": "refresh",
        "generation": 1,
        "lease_token": "first",
        "payload": {
            "queue_date": day,
            "_queue_phase": "prioritized_opening_item",
            "opening_plan": planned,
            "opening_index": 0,
        },
    }
    assert worker.execute_postgres_queue_refresh_slice(task)
    assert saved["opening_plan"] == planned
    assert saved["opening_index"] == 1
    assert not worker.execute_postgres_queue_refresh_slice(task)
    assert worker.execute_postgres_queue_refresh_slice(
        {**task, "lease_token": "second", "payload": dict(saved)}
    )
    assert connection.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 4


@pytest.mark.parametrize("value", [None, "0", "true", "yes", "1"])
def test_capacity_configuration_enables_only_explicit_one(monkeypatch, value):
    from app.services.opening_capacity_admission import capacity_expansion_enabled

    if value is None:
        monkeypatch.delenv("OPENING_CAPACITY_FRONTIER_EXPANSION", raising=False)
    else:
        monkeypatch.setenv("OPENING_CAPACITY_FRONTIER_EXPANSION", value)
    assert capacity_expansion_enabled() is (value == "1")


def test_capacity_worker_full_allowance_avoids_frontier_queries(
    capacity_database, monkeypatch
):
    from app.services import (
        postgres_queue_refresh as worker,
        opening_capacity_admission as capacity,
        study_mode,
    )

    connection, day, _ = capacity_database
    introduce_prefixes(connection, day, monkeypatch)
    connection.execute("UPDATE settings SET new_cards_per_day=2 WHERE id=1")
    monkeypatch.setattr(study_mode, "optional_work_paused", lambda database=None: False)
    monkeypatch.setattr(
        capacity,
        "read_capacity_routes",
        lambda *_: pytest.fail("Full allowance frontier read"),
    )

    def read(statement, parameters=(), *, native=False):
        if statement.startswith("WITH active_miss"):
            return []
        return connection.execute(statement.replace("%s", "?"), parameters).fetchall()

    monkeypatch.setattr(worker, "_bounded_read", read)
    assert worker._prepare_prioritized_openings(day) == []


def test_capacity_ready_publication_checks_translate_to_postgres_without_new_storage_contract(
    capacity_database, monkeypatch
):
    from app.postgres_store import postgres_sql
    from app.services.opening_capacity_admission import read_capacity_routes

    connection, day, _ = capacity_database
    introduce_prefixes(connection, day, monkeypatch)

    def read(statement, parameters):
        translated = postgres_sql(statement)
        assert translated.count("%s") == len(parameters)
        assert "?" not in translated
        return connection.execute(statement, parameters).fetchall()

    assert len(read_capacity_routes(read, "rep", ())) == 8
