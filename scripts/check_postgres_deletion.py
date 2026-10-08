"""Real PostgreSQL deletion, replay, escaped-null, and stale-worker regressions."""
from datetime import date, timedelta
import json
import os
import random
from pathlib import Path
import sys
import threading
import uuid

import chess
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import main, postgres_store, card_commands, repertoire_commands
from app.command_gateway import execute_command, read_operation
from app.card_deletion import RETAINED_REPERTOIRE_ID, permanent_delete_card
from app.services.durable_tasks import enqueue_task_in_transaction, lock_current_slice
from app.services.opening_graph import GraphInput, build_graph
from app.services.postgres_opening_graph import PreparedGraphLine, stage_graph_line_in_transaction
from app.models import ReviewRequest
from app.review_conflicts import ReviewConflict
from check_postgres_repertoire_limits import snapshot_queue_environment, restore_queue_environment


def main_check():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Deletion proof requires runner-owned disposable PostgreSQL")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_READ_URL"] = os.environ["TEMPO_DATABASE_WRITE_URL"]
    prefix = f"deletion-proof-{uuid.uuid4()}"
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    repertoire_ids = [f"{prefix}-{policy}" for policy in ("keep", "delete", "other", "graph")]
    keep, delete, other, graph = repertoire_ids
    card_ids = []
    task_ids = []
    operation_ids = []
    queue_snapshot = None
    main_ids = []
    game_task_snapshot = None
    retained_existed = False

    def command(name, payload):
        operation_id = str(uuid.uuid4())
        operation_ids.append(operation_id)
        result = execute_command(operation_id, name, payload)
        assert read_operation(operation_id)["state"] == "complete", read_operation(operation_id)
        assert execute_command(operation_id, name, payload) == result
        return result

    def prove_repeated_route_dependencies():
        repetition_repertoire = f"{prefix}-repetition"
        repetition_line = f"{prefix}-repetition-line"
        repertoire_ids.append(repetition_repertoire)
        repetition_fen = "4k1n1/8/8/7p/P7/8/8/4K1N1 w - - 0 1"
        repetition_moves = ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8", "a4a5", "h5h4", "e1f2"]
        repetition_source = {"id": repetition_line, "start_fen": repetition_fen,
            "moves_json": json.dumps(repetition_moves), "trained_color": "white"}
        repeated_steps = build_graph(GraphInput(repetition_repertoire, (repetition_source,), 1))
        assert repeated_steps[1].card_id == repeated_steps[3].card_id
        with postgres_store.connection() as database:
            assert not database.execute_native("SELECT 1 FROM cards WHERE id=ANY(%s)", ([step.card_id for step in repeated_steps],)).fetchone(), "Repetition proof identities collide with another fixture"
            card_ids.extend(step.card_id for step in repeated_steps)
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)", (repetition_repertoire, repetition_repertoire, "repetition proof", today))
            database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,'Repeated source','white',?,?,?)", (repetition_line, repetition_repertoire, repetition_fen, repetition_source["moves_json"], today))
            repeated_task = enqueue_task_in_transaction(database, "opening_graph_rebuild", repetition_repertoire,
                {"repertoire_id": repetition_repertoire, "local_day": today, "step_offset": 0})
            repeated_task["payload"] = json.loads(repeated_task["payload_json"])
            repeated_task["lease_token"] = "repetition-proof"
            task_ids.append(repeated_task["id"])
            database.execute("UPDATE background_tasks SET state='leased',lease_token=? WHERE id=?", (repeated_task["lease_token"], repeated_task["id"]))
            repeated_prepared = PreparedGraphLine(repetition_line, repeated_steps)
            assert stage_graph_line_in_transaction(database, repeated_task, repeated_prepared)
            database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(?,?,'ready',?)", (repetition_repertoire, repeated_task["generation"], today))
        command("cards.delete", {"card_id": repeated_steps[1].card_id, "expected_revision": 1})
        for restart in (False, True):
            postgres_store.close_pools()
            with postgres_store.connection() as database:
                if restart:
                    repeated_task["lease_token"] = "repetition-restarted"
                    database.execute("UPDATE background_tasks SET state='leased',phase='stage',lease_token=?,payload_json=? WHERE id=?", (repeated_task["lease_token"], json.dumps(repeated_task["payload"]), repeated_task["id"]))
                    assert stage_graph_line_in_transaction(database, repeated_task, repeated_prepared)
                for decision_index, expected_parent in ((2, repeated_steps[0].card_id), (4, repeated_steps[2].card_id)):
                    assert database.execute("SELECT parent_card_id FROM opening_graph_steps WHERE repertoire_id=? AND line_id=? AND decision_index=?", (repetition_repertoire, repetition_line, decision_index)).fetchone()[0] == expected_parent
                assert database.execute("SELECT moves_json FROM repertoire_lines WHERE id=?", (repetition_line,)).fetchone()[0] == repetition_source["moves_json"]
                assert database.execute("SELECT 1 FROM cards WHERE id=?", (repeated_steps[1].card_id,)).fetchone() is None
        with postgres_store.connection() as database:
            database.execute("UPDATE cards SET state='mature' WHERE id=?", (repeated_steps[0].card_id,))
            child_id = repeated_steps[2].card_id
            cursor = f"{int(child_id, 16) - 1:064x}"
            assert database.execute(f"SELECT id FROM cards WHERE {main._OPENING_UNLOCK_ELIGIBILITY_SQL} AND id>? ORDER BY id LIMIT 1", (cursor,)).fetchone()[0] == child_id
            main._unlock_eligible_opening_cards(database, today, after_card_id=cursor, batch_size=1)
            assert database.execute("SELECT state FROM cards WHERE id=?", (child_id,)).fetchone()[0] == "new"
            assert database.execute("SELECT state FROM cards WHERE id=?", (repeated_steps[4].card_id,)).fetchone()[0] == "locked"
        print("PASS test_deleted_repeated_route_prerequisite_reparents_each_occurrence_without_self_dependency; permanent deletion, reconnect/rebuild and real descendant unlock")

    try:
        with postgres_store.connection() as database:
            queue_snapshot = snapshot_queue_environment(database, (today, tomorrow))
            main_ids = [row[0] for row in database.execute("SELECT id FROM repertoires WHERE is_main=1")]
            retained_existed = database.execute("SELECT 1 FROM repertoires WHERE id=?", (RETAINED_REPERTOIRE_ID,)).fetchone() is not None
            game_task = database.execute("SELECT * FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'").fetchone()
            game_task_snapshot = (dict(game_task), [dict(row) for row in database.execute("SELECT * FROM background_task_events WHERE task_id=? ORDER BY id", (game_task["id"],))]) if game_task else None
            for repertoire_id in repertoire_ids:
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)", (repertoire_id, repertoire_id, "synthetic deletion proof", today))
            database.execute("UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END", (other,))
            for repertoire_id in (keep, delete):
                for suffix, learned in (("learned", True), ("new", False), ("shared", True), ("archived", True), ("introduced", True)):
                    identifier = f"{repertoire_id}-{suffix}"
                    card_ids.append(identifier)
                    database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,interval_days,archived) VALUES(?,?,'prefix',?,'[\"e2e4\"]','mature',?,?,7,?)", (identifier, repertoire_id, chess.STARTING_FEN, tomorrow, today if learned else None, int(suffix == "archived")))
                    database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (repertoire_id, identifier))
                    database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_repertoire_id) VALUES(?,?,?,?)", (today, identifier, 100000 + len(card_ids), repertoire_id))
                    if suffix == "introduced":
                        database.execute("UPDATE cards SET state='learning',due_date=? WHERE id=?", (today, identifier))
                    if learned and suffix != "introduced":
                        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,0,7)", (identifier, today))
                    if suffix == "shared":
                        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (other, identifier))
            target_task = enqueue_task_in_transaction(database, "opening_graph_rebuild", keep, {"repertoire_id": keep})
            unrelated_task = enqueue_task_in_transaction(database, "coverage", other, {"repertoire_id": other, "position": f"{keep}\0position", "nested": {"repertoire_id": keep}})
            task_ids.extend((target_task["id"], unrelated_task["id"]))
            target_task["lease_token"] = "stale-graph-lease"
            database.execute("UPDATE background_tasks SET state='leased',lease_token=? WHERE id=?", (target_task["lease_token"], target_task["id"]))
        for repertoire_id, policy in ((keep, "keep"), (delete, "delete")):
            assert command("repertoires.delete", {"repertoire_id": repertoire_id, "learned_cards": policy}) == {"deleted": True, "id": repertoire_id}
        with postgres_store.connection() as database:
            assert not lock_current_slice(database, target_task)
            assert database.execute("SELECT state FROM background_tasks WHERE id=?", (unrelated_task["id"],)).fetchone()[0] == "queued"
            assert database.execute("SELECT is_main FROM repertoires WHERE id=?", (other,)).fetchone()[0] == 1
            for repertoire_id in (keep, delete):
                assert database.execute("SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)).fetchone() is None
                assert database.execute("SELECT 1 FROM cards WHERE id=?", (f"{repertoire_id}-new",)).fetchone() is None
                assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (f"{repertoire_id}-shared",)).fetchone()[0] == 1
                assert database.execute("SELECT repertoire_id FROM cards WHERE id=?", (f"{repertoire_id}-shared",)).fetchone()[0] == other
                assert database.execute("SELECT 1 FROM deleted_cards WHERE card_id=?", (f"{repertoire_id}-shared",)).fetchone() is None
                try:
                    main._apply_review(f"{repertoire_id}-new", ReviewRequest(outcome="correct"), database=database)
                except ReviewConflict as error:
                    assert error.code == "card_deleted"
                else:
                    raise AssertionError("Delayed repertoire review graded deleted content")
            assert tuple(database.execute("SELECT repertoire_id,interval_days,due_date FROM cards WHERE id=?", (f"{keep}-learned",)).fetchone()) == (RETAINED_REPERTOIRE_ID, 7, tomorrow)
            assert database.execute("SELECT archived FROM cards WHERE id=?", (f"{keep}-archived",)).fetchone()[0] == 1
            assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (f"{keep}-learned",)).fetchone()[0] == 1
            assert database.execute("SELECT 1 FROM cards WHERE id=?", (f"{delete}-learned",)).fetchone() is None
            from app.services.postgres_queue_refresh import _reconcile_one_unseen_entry, _admit_one_due_card
            introduced_id = f"{keep}-introduced"
            entry = database.execute("SELECT id,card_id,admission_repertoire_id AS repertoire_id FROM daily_queue WHERE card_id=?", (introduced_id,)).fetchone()
            assert not _reconcile_one_unseen_entry(database, today, dict(entry), 0)
            assert database.execute("SELECT 1 FROM daily_queue WHERE id=?", (entry["id"],)).fetchone()
            main._reset_stale_opening_introductions(database, tomorrow)
            assert tuple(database.execute("SELECT state,introduced_at,trained_color FROM cards WHERE id=?", (introduced_id,)).fetchone()) == ("learning", today, "white")
            _admit_one_due_card(database, tomorrow, introduced_id)
            assert database.execute("SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=?", (introduced_id, tomorrow)).fetchone()
        print("PASS test_repertoire_delete_ignores_unrelated_null_job_payload; exact cancellation fences stale workers")
        print("PASS test_repertoire_keep_delete_preserves_shared_schedules_history_and_non_main_selection; durable policy replay")
        print("PASS test_retained_introduced_cards_survive_queue_refresh_and_next_day_without_a_review")

        line_id = f"{prefix}-line"
        fixture_random = random.Random(20261008)
        board = chess.Board()
        for _ in range(16):
            board.push(fixture_random.choice(list(board.legal_moves)))
        starting_fen = board.fen()
        moves = []
        for _ in range(5):
            selected_move = fixture_random.choice(list(board.legal_moves))
            moves.append(selected_move.uci())
            board.push(selected_move)
        source = {"id": line_id, "start_fen": starting_fen, "moves_json": json.dumps(moves), "trained_color": "white"}
        steps = build_graph(GraphInput(graph, (source,), 1))
        with postgres_store.connection() as database:
            assert not database.execute_native("SELECT 1 FROM cards WHERE id=ANY(%s)", ([step.card_id for step in steps],)).fetchone(), "Proof identities collide with another fixture"
            card_ids.extend(step.card_id for step in steps)
            database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,'Proof','white',?,?,?)", (line_id, graph, source["start_fen"], source["moves_json"], today))
            task = enqueue_task_in_transaction(database, "opening_graph_rebuild", graph, {"repertoire_id": graph, "local_day": today, "step_offset": 0})
            task["payload"] = json.loads(task["payload_json"])
            task_ids.append(task["id"])
            task["lease_token"] = "graph-proof"
            database.execute("UPDATE background_tasks SET state='leased',lease_token=? WHERE id=?", (task["lease_token"], task["id"]))
            prepared = PreparedGraphLine(line_id, steps)
        contention_result = []
        def attempt_contended_slice():
            try:
                with postgres_store.connection(background=True) as background_database:
                    stage_graph_line_in_transaction(background_database, task, prepared)
            except Exception as error:
                contention_result.append(error)
        with postgres_store.connection() as foreground_database:
            foreground_database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{steps[0].card_id}",))
            contender = threading.Thread(target=attempt_contended_slice)
            contender.start()
            with postgres_store.connection(read_only=True) as reader:
                assert reader.execute("SELECT 1 FROM repertoires WHERE id=?", (graph,)).fetchone()
            contender.join(timeout=5)
            assert not contender.is_alive() and len(contention_result) == 1 and isinstance(contention_result[0], (psycopg.errors.LockNotAvailable, psycopg.errors.TransactionTimeout)), f"Background slice did not yield to the foreground deletion lock: {contention_result}"
        with postgres_store.connection() as database:
            assert stage_graph_line_in_transaction(database, task, prepared)
            database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(?,?,'ready',?)", (graph, task["generation"], today))
            assert database.execute("SELECT state FROM cards WHERE id=?", (steps[1].card_id,)).fetchone()[0] == "locked"
        print("PASS test_permanent_card_deletion_foreground_contention_yields_and_replays_idempotently")
        assert command("cards.delete", {"card_id": steps[0].card_id, "expected_revision": 1})["deleted"]
        with postgres_store.connection() as database:
            assert database.execute("SELECT moves_json FROM repertoire_lines WHERE id=?", (line_id,)).fetchone()[0] == source["moves_json"]
            assert database.execute("SELECT parent_card_id FROM opening_graph_steps WHERE card_id=?", (steps[1].card_id,)).fetchone()[0] is None
            unlock_cursor = f"{int(steps[1].card_id, 16) - 1:064x}"
            assert database.execute(f"SELECT id FROM cards WHERE {main._OPENING_UNLOCK_ELIGIBILITY_SQL} AND id>? ORDER BY id LIMIT 1", (unlock_cursor,)).fetchone()[0] == steps[1].card_id
            main._unlock_eligible_opening_cards(database, today, after_card_id=unlock_cursor, batch_size=1)
            assert database.execute("SELECT state FROM cards WHERE id=?", (steps[1].card_id,)).fetchone()[0] == "new"
            assert database.execute("SELECT state FROM cards WHERE id=?", (steps[2].card_id,)).fetchone()[0] == "locked"
            assert database.execute("SELECT COUNT(*) FROM opening_evidence_presentations WHERE card_id=?", (steps[0].card_id,)).fetchone()[0] == 0
            with database.raw.transaction():
                try:
                    database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES(?,?,'prefix',?,'[]','new',?)", (steps[0].card_id, graph, chess.STARTING_FEN, today))
                except psycopg.errors.CheckViolation as error:
                    assert error.diag.constraint_name == "deleted_card_content"
                    raise
                else:
                    raise AssertionError("Deleted content was recreated")
    except psycopg.errors.CheckViolation:
        # The failed insert's nested transaction has rolled back. Reconnect to
        # prove the exclusion survives connection/worker restart and stale input.
        with postgres_store.connection() as database:
            assert database.execute("SELECT 1 FROM deleted_cards WHERE card_id=?", (steps[0].card_id,)).fetchone()
            database.execute("UPDATE background_tasks SET state='leased',phase='stage',lease_token=?,payload_json=? WHERE id=?", (task["lease_token"], json.dumps(task["payload"]), task["id"]))
            assert stage_graph_line_in_transaction(database, task, prepared)
            assert database.execute("SELECT 1 FROM cards WHERE id=?", (steps[0].card_id,)).fetchone() is None
            assert database.execute("SELECT parent_card_id FROM opening_graph_steps WHERE card_id=?", (steps[1].card_id,)).fetchone()[0] is None
            try:
                main._apply_review(steps[0].card_id, ReviewRequest(outcome="correct"), database=database)
            except ReviewConflict as error:
                assert error.code == "card_deleted"
            else:
                raise AssertionError("Delayed review graded deleted content")
        assert command("cards.delete", {"card_id": f"{keep}-shared", "expected_revision": 1})["deleted"]
        print("PASS test_permanent_card_delete_global_history_rebuild_restart_descendant_usability_and_delayed_review")
        from app.pgn_import_commands import prepare_import_payload
        from app.services.pgn import ParsedLine
        import_payload = prepare_import_payload(f"{prefix}-import.pgn", "white", 2, 1,
            [ParsedLine(starting_fen, moves, ())])
        with postgres_store.connection() as database:
            from app.card_deletion import purge_card_data
            import_ids = import_payload["segment_ids"]
            card_ids.extend(import_ids)
            purge_card_data(database, import_ids)
        imported = command("imports.pgn.admit", import_payload)
        repertoire_ids.append(imported["repertoire_id"])
        assert imported["cards_created"] == imported["prefix_cards_created"] == imported["decision_cards_created"] == 0
        assert imported["shared_prefixes_reused"] == imported["shared_decisions_reused"] == 0
        with postgres_store.connection() as database:
            from app.card_deletion import cancel_repertoire_tasks
            cancel_repertoire_tasks(database, imported["repertoire_id"])
            task_ids.extend(row[0] for row in database.execute("SELECT id FROM background_tasks WHERE deduplication_key=?", (imported["repertoire_id"],)))
        print("PASS test_import_does_not_report_deleted_content_as_created_or_shared")
        prove_repeated_route_dependencies()
    finally:
        with postgres_store.connection() as database:
            if card_ids:
                from app.card_deletion import purge_card_data
                purge_card_data(database, list(set(card_ids)))
                database.execute_native("DELETE FROM deleted_cards WHERE card_id=ANY(%s)", (card_ids,))
                database.execute_native("DELETE FROM queue_attempt_origins WHERE card_id=ANY(%s)", (card_ids,))
            database.execute_native("DELETE FROM background_tasks WHERE id=ANY(%s)", (task_ids,))
            database.execute_native("DELETE FROM operation_receipts WHERE operation_id=ANY(%s)", (operation_ids,))
            database.execute_native("DELETE FROM repertoires WHERE id=ANY(%s)", (repertoire_ids,))
            if not retained_existed:
                database.execute("DELETE FROM repertoires WHERE id=?", (RETAINED_REPERTOIRE_ID,))
            database.execute_native("UPDATE repertoires SET is_main=CASE WHEN id=ANY(%s) THEN 1 ELSE 0 END", (main_ids,))
            database.execute("DELETE FROM background_tasks WHERE kind='repertoire_game_refresh' AND deduplication_key='all'")
            if game_task_snapshot:
                for table, rows in (("background_tasks", [game_task_snapshot[0]]), ("background_task_events", game_task_snapshot[1])):
                    for row in rows:
                        database.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
            if queue_snapshot:
                restore_queue_environment(database, queue_snapshot)


if __name__ == "__main__":
    main_check()
