"""Prove capture publication, contention, rollback, and receipt replay on disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import os
from pathlib import Path
import sys
import threading
import uuid

import chess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store, tactic_commands  # registers the production handler
from app.command_gateway import execute_command, read_operation
from app.services.postgres_queue_refresh import _publish_tactical_introduction
from app.services.tactic_admission import count_daily_tactic_introductions
from app.services.cards import card_id


def main():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Capture proof requires the disposable PostgreSQL instance")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_READ_URL"] = os.environ["TEMPO_DATABASE_WRITE_URL"]
    day = date.today().isoformat()
    request = {"capture_id": str(uuid.uuid4()), "starting_fen": chess.STARTING_FEN,
               "moves": ["e2e4"], "source_kind": "manual", "note": "durability synthetic"}
    payload = {"request": request}
    operation_id = request["capture_id"]
    with postgres_store.connection() as database:
        original_limit = database.execute("SELECT tactics_new_per_day FROM settings WHERE id=1").fetchone()[0]
        initial_count = count_daily_tactic_introductions(database, day)
        database.execute("UPDATE settings SET tactics_new_per_day=?", (initial_count + 1,))
        database.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES('capture-synthetic',1)")
        rotation = database.execute("SELECT last_pack_id FROM tactic_rotation WHERE id=1").fetchone()[0]
    automatic_fen = chess.Board(); automatic_fen.push_uci("a2a3")
    automatic_moves = ["a7a6"]
    prepared = {"pack_id": "capture-synthetic", "puzzle_id": "capture-synthetic",
                "card_id": card_id(automatic_fen.fen(), automatic_moves),
                "training_fen": automatic_fen.fen(), "source_fen": chess.STARTING_FEN,
                "solution_json": json.dumps(automatic_moves), "rotation_cursor": rotation}
    # Prepare outside a connection, then publish concurrently with a foreground capture.
    rendezvous = threading.Barrier(2)
    def publish():
        rendezvous.wait(timeout=5)
        with postgres_store.connection() as database:
            return _publish_tactical_introduction(database, day, prepared)
    def capture():
        rendezvous.wait(timeout=5)
        return execute_command(operation_id, "tactics.capture.create", payload)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            captured_future = workers.submit(capture)
            published_future = workers.submit(publish)
            captured = captured_future.result(timeout=10)
            automatic_published = published_future.result(timeout=10)
        assert captured and captured["queued"]
        with postgres_store.connection(read_only=True) as database:
            assert count_daily_tactic_introductions(database, day) == initial_count + 1 + int(automatic_published)
            entries_before = [tuple(row) for row in database.execute("SELECT * FROM daily_queue ORDER BY id")]
            assert database.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (operation_id,)).fetchone()[0] == 1
            assert database.execute("SELECT attempt_state FROM daily_queue WHERE card_id=?", (captured["card_id"],)).fetchone()[0] == "guided"
        postgres_store.close_pools()  # restart the client's persistence boundary
        assert execute_command(operation_id, "tactics.capture.create", payload) == captured
        assert read_operation(operation_id)["state"] == "complete"
        with postgres_store.connection(read_only=True) as database:
            assert entries_before == [tuple(row) for row in database.execute("SELECT * FROM daily_queue ORDER BY id")]
        # A failing command must retain its failed receipt and no business effects.
        invalid = {**request, "capture_id": str(uuid.uuid4()), "moves": ["e2e5"]}
        assert execute_command(invalid["capture_id"], "tactics.capture.create", {"request": invalid}) is None
        assert read_operation(invalid["capture_id"])["state"] == "failed"
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (invalid["capture_id"],)).fetchone()[0] == 0
            assert entries_before == [tuple(row) for row in database.execute("SELECT * FROM daily_queue ORDER BY id")]
        # Existing-card collision rolls back the repertoire/event/schedule/queue changes.
        with postgres_store.connection() as database:
            database.execute("UPDATE cards SET archived=1 WHERE id=?", (captured["card_id"],))
        collision = {**request, "capture_id": str(uuid.uuid4())}
        assert execute_command(collision["capture_id"], "tactics.capture.create", {"request": collision}) is None
        assert read_operation(collision["capture_id"])["error"]["status_code"] == 409
        with postgres_store.connection() as database:
            assert database.execute("SELECT COUNT(*) FROM tactic_captures WHERE id=?", (collision["capture_id"],)).fetchone()[0] == 0
            database.execute("UPDATE cards SET archived=0 WHERE id=?", (captured["card_id"],))
            database.execute("UPDATE daily_queue SET status='complete' WHERE card_id=?", (captured["card_id"],))
            database.execute("UPDATE settings SET tactics_new_per_day=0")
        recapture = {**request, "capture_id": str(uuid.uuid4())}
        result = execute_command(recapture["capture_id"], "tactics.capture.create", {"request": recapture})
        assert result["reused"] and not result["introduced"]
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND status='queued'", (captured["card_id"],)).fetchone()[0] == 1
    finally:
        with postgres_store.connection() as database:
            database.execute("UPDATE settings SET tactics_new_per_day=?", (original_limit,))
            database.execute("DELETE FROM tactic_pack_activation WHERE pack_id='capture-synthetic'")
            # Retain event/card rows for backup comparison, but retire this scenario's
            # queue entries before the independent foreground study scenario begins.
            fixture_card_ids = (card_id(request["starting_fen"], request["moves"]), prepared["card_id"])
            database.execute("UPDATE cards SET archived=1 WHERE id IN (?,?)", fixture_card_ids)
            database.execute("UPDATE daily_queue SET status='complete' WHERE card_id IN (?,?)", fixture_card_ids)
        postgres_store.close_pools()
    print("PASS capture/automatic contention, quota recheck, restart and receipt replay, atomic rejection, and finished queue reopen")


if __name__ == "__main__":
    main()
